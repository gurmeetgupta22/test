import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'package:serious_python/serious_python.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:webview_flutter/webview_flutter.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // Start the embedded Python bot engine on iOS.
  // On Android this is a no-op — Chaquopy handles everything via MethodChannel.
  if (Platform.isIOS) {
    try {
      unawaited(
        SeriousPython.run(
          appFileName: 'main_ios.py',
        ).catchError((Object err) {
          debugPrint('SeriousPython iOS server startup error: $err');
          return null;
        }),
      );
    } catch (e) {
      debugPrint('SeriousPython startup exception: $e');
    }
  }
  runApp(const MaxAlphaApp());
}

class ApiError implements Exception {
  ApiError(this.message);
  final String message;
}

// ── iOS HTTP bridge ──────────────────────────────────────────────────────────
// serious_python starts the Flask server (main_ios.py) in a background thread.
// Flutter talks to it via HTTP on 127.0.0.1:8766.
// Android keeps the existing MethodChannel / Chaquopy path.

const _iOSBotBaseUrl = 'http://127.0.0.1:8766';

Future<Map<String, dynamic>> _iosGet(String path) async {
  final uri = Uri.parse('$_iOSBotBaseUrl$path');
  late http.Response resp;
  // The Python server may need a moment to start on first call — retry briefly.
  for (var attempt = 0; attempt < 8; attempt++) {
    try {
      resp = await http.get(uri).timeout(const Duration(seconds: 10));
      break;
    } catch (_) {
      if (attempt == 7) rethrow;
      await Future<void>.delayed(const Duration(milliseconds: 500));
    }
  }
  final body = jsonDecode(resp.body) as Map<String, dynamic>;
  if (body['ok'] != true) throw ApiError(body['error']?.toString() ?? 'iOS bot error on $path');
  final result = body['result'];
  if (result == null) return const {};
  if (result is Map<String, dynamic>) return result;
  return {'result': result};
}

Future<Map<String, dynamic>> _iosPost(String path, [Object? body]) async {
  final uri = Uri.parse('$_iOSBotBaseUrl$path');
  late http.Response resp;
  for (var attempt = 0; attempt < 8; attempt++) {
    try {
      resp = await http
          .post(uri,
              headers: {'Content-Type': 'application/json'},
              body: jsonEncode(body ?? {}))
          .timeout(const Duration(seconds: 30));
      break;
    } catch (_) {
      if (attempt == 7) rethrow;
      await Future<void>.delayed(const Duration(milliseconds: 500));
    }
  }
  final decoded = jsonDecode(resp.body) as Map<String, dynamic>;
  if (decoded['ok'] != true) throw ApiError(decoded['error']?.toString() ?? 'iOS bot error on $path');
  final result = decoded['result'];
  if (result == null) return const {};
  if (result is Map<String, dynamic>) return result;
  return {'result': result};
}

// ── MobileApi ────────────────────────────────────────────────────────────────

class MobileApi {
  MobileApi(this.session);
  final Session session;

  // Android: direct Chaquopy MethodChannel bridge.
  static const _bridge = MethodChannel('com.maxalpha.mobile/bot');

  /// Call via MethodChannel (Android) or HTTP (iOS).
  Future<T?> _call<T>(String method, [dynamic arguments]) async {
    if (Platform.isIOS) {
      // Route to the Flask HTTP bridge.
      final Map<String, dynamic> result;
      switch (method) {
        case 'configure':
          result = await _iosPost('/configure', arguments);
          break;
        case 'dashboard':
          result = await _iosGet('/dashboard');
          return result as T?;
        case 'logs':
          result = await _iosGet('/logs');
          return result as T?;
        case 'signals':
          result = await _iosGet('/signals');
          return result as T?;
        case 'startDashboard':
          result = await _iosPost('/startDashboard');
          return result as T?;
        case 'startBot':
          await _iosPost('/startBot', arguments);
          return null;
        case 'stopBot':
          await _iosPost('/stopBot');
          return null;
        default:
          throw ApiError('Unknown bot method: $method');
      }
      return result as T?;
    }
    // Android path — unchanged.
    try {
      return await _bridge.invokeMethod<T>(method, arguments);
    } on PlatformException catch (error) {
      throw ApiError(error.message ?? 'The local Python engine could not complete $method.');
    } on MissingPluginException {
      throw ApiError('The local Python engine is available only in the installed Android or iOS app, not Chrome.');
    }
  }

  Future<Map<String, dynamic>> config() async => {'configured': session.configured};
  Future<void> saveConfig(Map<String, String> value) async {
    await _call<dynamic>('configure', value);
    await session.saveBotConfig(value);
  }

  Future<Map<String, dynamic>> dashboard() async =>
      Map<String, dynamic>.from(await _call<Map>('dashboard') ?? const {});
  Future<Map<String, dynamic>> logs() async =>
      Map<String, dynamic>.from(await _call<Map>('logs') ?? const {});
  Future<Map<String, dynamic>> signals() async =>
      Map<String, dynamic>.from(await _call<Map>('signals') ?? const {});
  Future<Map<String, dynamic>> startDashboard() async =>
      Map<String, dynamic>.from(await _call<Map>('startDashboard') ?? const {});
  Future<void> start(double? amount) async {
    await _call<dynamic>('startBot', {'budget': amount, 'configuration': session.botConfig});
  }

  Future<void> stop() => _call<dynamic>('stopBot');

  Future<void> logout() async {}
}

class Session extends ChangeNotifier {
  static const _secure = FlutterSecureStorage();
  late SharedPreferences _prefs;
  String deviceId = '';
  // Deliberately memory-only: this unlock is valid while this Flutter process
  // is alive (including background/resume), but never survives a full relaunch.
  String? _activeSessionToken;
  bool get isUnlocked => _activeSessionToken != null;
  late MobileApi api;
  bool dark = false;
  bool ready = false;
  bool configured = false;
  String? dashboardUrl;
  Map<String, String> botConfig = {};

  Future<void> load() async {
    _prefs = await SharedPreferences.getInstance();
    deviceId = _prefs.getString('device_id') ?? _id();
    // Never restore an unlock from disk. The token is created after a successful
    // password check and remains in this in-memory Session for the app lifetime.
    _activeSessionToken = null;
    dark = _prefs.getBool('dark_theme') ?? false;
    configured = _prefs.getBool('bot_configured') ?? false;
    botConfig = {
      'trading_mode': _prefs.getString('trading_mode') ?? 'paper',
      'dhan_client_id': await _secure.read(key: 'dhan_client_id') ?? '',
      'dhan_access_token': await _secure.read(key: 'dhan_access_token') ?? '',
      'ai_key': await _secure.read(key: 'ai_key') ?? '',
      'council_mode': _prefs.getString('council_mode') ?? 'local',
    };
    api = MobileApi(this);
    await _prefs.setString('device_id', deviceId);
    ready = true;
    notifyListeners();
  }

  String _id() => List.generate(
    32,
    (_) => 'abcdef0123456789'[Random.secure().nextInt(16)],
  ).join();

  String _newSessionToken() => List.generate(
    64,
    (_) => 'abcdef0123456789'[Random.secure().nextInt(16)],
  ).join();

  Future<String> createAccount(String email) async {
    final clean = email.trim().toLowerCase();
    if (!clean.contains('@')) throw ApiError('Enter a valid email address.');
    final stem = clean.split('@').first.replaceAll(RegExp(r'[^a-z0-9]'), '');
    if (stem.isEmpty) throw ApiError('Enter an email address with letters or numbers before @.');
    final password = '${stem.substring(0, min(5, stem.length))}@${100000 + Random.secure().nextInt(900000)}';
    await _prefs.setString('account_email', clean);
    await _secure.write(key: 'account_password', value: password);
    return password;
  }

  Future<void> login(String email, String password) async {
    if (email.trim().toLowerCase() != _prefs.getString('account_email') || password != await _secure.read(key: 'account_password')) {
      throw ApiError('Email or password is incorrect for this device.');
    }
    _activeSessionToken = _newSessionToken();
    notifyListeners();
  }

  Future<void> saveBotConfig(Map<String, String> value) async {
    botConfig = {...botConfig, ...value};
    configured = true;
    for (final entry in botConfig.entries) {
      if (const {'dhan_client_id', 'dhan_access_token', 'ai_key'}.contains(entry.key)) {
        await _secure.write(key: entry.key, value: entry.value);
      } else {
        await _prefs.setString(entry.key, entry.value);
      }
    }
    await _prefs.setBool('bot_configured', true);
  }

  Future<void> signOut() async {
    _activeSessionToken = null;
    notifyListeners();
  }

  void setDashboardUrl(String? value) {
    dashboardUrl = value;
    notifyListeners();
  }

  Future<void> toggleTheme(bool value) async {
    dark = value;
    await _prefs.setBool('dark_theme', value);
    notifyListeners();
  }
}

class MaxAlphaApp extends StatefulWidget {
  const MaxAlphaApp({super.key});
  @override
  State<MaxAlphaApp> createState() => _MaxAlphaAppState();
}

class _MaxAlphaAppState extends State<MaxAlphaApp> {
  final session = Session();
  @override
  void initState() {
    super.initState();
    session.load();
  }

  @override
  Widget build(BuildContext context) {
    final light = _theme(Brightness.light);
    final dark = _theme(Brightness.dark);
    return AnimatedBuilder(
      animation: session,
      builder: (context, child) => MaterialApp(
        debugShowCheckedModeBanner: false,
        title: 'Max Alpha',
        theme: light,
        darkTheme: dark,
        themeMode: session.dark ? ThemeMode.dark : ThemeMode.light,
        home: !session.ready
            ? const Scaffold(body: Center(child: CircularProgressIndicator()))
            : !session.isUnlocked
            ? AuthPage(session: session)
            : AppShell(session: session),
      ),
    );
  }

  ThemeData _theme(Brightness brightness) {
    final isDark = brightness == Brightness.dark;
    final scheme = ColorScheme.fromSeed(
      seedColor: const Color(0xfff97316),
      brightness: brightness,
      surface: isDark ? const Color(0xff0b1729) : const Color(0xfff8fafc),
    );
    return ThemeData(
      useMaterial3: true,
      brightness: brightness,
      colorScheme: scheme,
      scaffoldBackgroundColor: isDark ? const Color(0xff07111f) : const Color(0xfff5f7fb),
      appBarTheme: AppBarTheme(
        elevation: 0,
        centerTitle: false,
        backgroundColor: isDark ? const Color(0xff0b1729) : Colors.white,
        foregroundColor: isDark ? const Color(0xffedf5ff) : const Color(0xff10233d),
        titleTextStyle: const TextStyle(fontFamily: 'monospace', fontWeight: FontWeight.bold, fontSize: 18, letterSpacing: 1.2),
      ),
      cardTheme: CardThemeData(
        elevation: 0,
        color: isDark ? const Color(0xff10233d) : Colors.white,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20), side: BorderSide(color: isDark ? const Color(0x284a74a9) : const Color(0xffe5e7eb))),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: isDark ? const Color(0xff10233d) : const Color(0xfff8fafc),
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 17),
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: BorderSide.none),
        enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: BorderSide(color: isDark ? const Color(0x334a74a9) : const Color(0xffe5e7eb))),
        focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: const BorderSide(color: Color(0xfff97316), width: 1.5)),
      ),
      filledButtonTheme: FilledButtonThemeData(style: FilledButton.styleFrom(
        minimumSize: const Size.fromHeight(54),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        textStyle: const TextStyle(fontWeight: FontWeight.w700, letterSpacing: .2),
      )),
      snackBarTheme: SnackBarThemeData(behavior: SnackBarBehavior.floating, shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14))),
    );
  }
}

class AuthPage extends StatefulWidget {
  const AuthPage({super.key, required this.session});
  final Session session;
  @override
  State<AuthPage> createState() => _AuthPageState();
}

class _AuthPageState extends State<AuthPage> {
  final email = TextEditingController(), password = TextEditingController();
  bool register = false, waiting = false;
  String? error;

  Future<void> submit() async {
    setState(() {
      waiting = true;
      error = null;
    });
    try {
      if (register) {
        final generated = await widget.session.createAccount(email.text);
        if (!mounted) return;
        await showDialog<void>(
          context: context,
          barrierDismissible: false,
          builder: (ctx) => AlertDialog(
            title: const Text('Save this password'),
            content: SelectableText(
              'Your Max Alpha password is:\n\n$generated\n\nIt cannot be changed. It is stored only on this device.',
            ),
            actions: [
              FilledButton(
                onPressed: () => Navigator.pop(ctx),
                child: const Text('I saved it'),
              ),
            ],
          ),
        );
        setState(() => register = false);
      } else {
        await widget.session.login(email.text, password.text);
      }
    } on ApiError catch (e) {
      if (mounted) setState(() => error = e.message);
    } finally {
      if (mounted) setState(() => waiting = false);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    body: Container(
      decoration: BoxDecoration(gradient: LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: Theme.of(context).brightness == Brightness.dark ? const [Color(0xff07111f), Color(0xff10233d), Color(0xff07111f)] : const [Color(0xfffffbf7), Color(0xfff5f7fb)])),
      child: Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 430),
          child: Card(
            child: Padding(
              padding: const EdgeInsets.all(28),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Container(width: 72, height: 72, decoration: BoxDecoration(color: const Color(0x20f97316), borderRadius: BorderRadius.circular(22)), child: const Icon(Icons.auto_graph_rounded, size: 38, color: Color(0xfff97316))),
                  const SizedBox(height: 12),
                  Text(
                    'MAX ALPHA',
                    style: Theme.of(context).textTheme.headlineSmall?.copyWith(fontFamily: 'monospace', fontWeight: FontWeight.bold, letterSpacing: 1.5),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    register
                        ? 'Create your device-bound account'
                        : 'Sign in to your trading console',
                  ),
                  const SizedBox(height: 24),
                  TextField(
                    controller: email,
                    keyboardType: TextInputType.emailAddress,
                    decoration: const InputDecoration(
                      labelText: 'Email address',
                    ),
                  ),
                  if (!register) ...[
                    const SizedBox(height: 12),
                    TextField(
                      controller: password,
                      obscureText: true,
                      decoration: const InputDecoration(labelText: 'Password'),
                    ),
                  ],
                  if (error != null)
                    Padding(
                      padding: const EdgeInsets.only(top: 12),
                      child: Text(
                        error!,
                        style: const TextStyle(color: Colors.red),
                      ),
                    ),
                  const SizedBox(height: 20),
                  FilledButton.icon(
                    onPressed: waiting ? null : submit,
                    icon: Icon(register ? Icons.key_rounded : Icons.arrow_forward_rounded),
                    label: Text(
                      register ? 'Generate device password' : 'Sign in',
                    ),
                  ),
                  TextButton(
                    onPressed: waiting
                        ? null
                        : () => setState(() {
                            register = !register;
                            error = null;
                          }),
                    child: Text(
                      register
                          ? 'I already have an account'
                          : 'First time? Create an account',
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
      ),
    ),
  );
}

class AppShell extends StatefulWidget {
  const AppShell({super.key, required this.session});
  final Session session;
  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  int page = 0;
  bool setupChecked = false, setupNeeded = false;
  late final List<Widget> _pages;
  @override
  void initState() {
    super.initState();
    // Keep every section mounted. In particular, this preserves the WebView
    // dashboard and its Flutter-provided data across drawer navigation.
    _pages = [
      DashboardPage(session: widget.session),
      RunPage(session: widget.session),
      ExactDashboardPage(session: widget.session),
      SettingsPage(
        session: widget.session,
        onLogout: () => widget.session.signOut(),
      ),
    ];
    _checkSetup();
  }

  Future<void> _checkSetup() async {
    if (mounted) {
      setState(() {
        setupNeeded = !widget.session.configured;
        setupChecked = true;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    if (!setupChecked) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }
    final title = setupNeeded ? 'Bot setup' : ['Dashboard', 'Run MaxAlpha', 'HTML Dashboard', 'Settings'][page];
    return Scaffold(
      appBar: AppBar(
        toolbarHeight: 68,
        titleSpacing: 8,
        title: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisAlignment: MainAxisAlignment.center, children: [
          Text(title, style: const TextStyle(fontFamily: 'monospace', fontWeight: FontWeight.w700, fontSize: 17, letterSpacing: .4)),
          const SizedBox(height: 2),
          Text(page == 1 && !setupNeeded ? 'LOCAL ENGINE CONTROL' : 'MAX ALPHA', style: TextStyle(color: Theme.of(context).colorScheme.outline, fontSize: 9, letterSpacing: 1.1)),
        ]),
        leading: Builder(builder: (ctx) => IconButton(icon: const Icon(Icons.menu_rounded), onPressed: () => Scaffold.of(ctx).openDrawer())),
      ),
      drawer: Drawer(
        backgroundColor: Theme.of(context).brightness == Brightness.dark ? const Color(0xff091625) : Colors.white,
        shape: const RoundedRectangleBorder(borderRadius: BorderRadius.horizontal(right: Radius.circular(28))),
        child: ListView(
          padding: EdgeInsets.zero,
          children: [
            Container(
              height: 175,
              padding: const EdgeInsets.fromLTRB(24, 56, 24, 20),
              decoration: const BoxDecoration(gradient: LinearGradient(colors: [Color(0xff10233d), Color(0xff07111f)])),
              child: const Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Icon(Icons.auto_graph_rounded, color: Color(0xfff97316), size: 30),
                Spacer(),
                Text('MAX ALPHA', style: TextStyle(color: Colors.white, fontFamily: 'monospace', fontWeight: FontWeight.bold, fontSize: 20, letterSpacing: 1.4)),
                SizedBox(height: 4), Text('LOCAL PYTHON TRADING', style: TextStyle(color: Color(0xff9fb4d0), fontSize: 10, letterSpacing: 1.1)),
              ]),
            ),
            const SizedBox(height: 12),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              child: Text('WORKSPACE', style: TextStyle(color: Theme.of(context).colorScheme.outline, fontSize: 10, fontWeight: FontWeight.bold, letterSpacing: 1.2)),
            ),
            const SizedBox(height: 6),
            _nav(0, Icons.grid_view_rounded, 'Dashboard'),
            _nav(1, Icons.play_circle_fill_rounded, 'Run MaxAlpha'),
            _nav(2, Icons.web_rounded, 'HTML Web View'),
            const Divider(indent: 20, endIndent: 20),
            _nav(3, Icons.tune_rounded, 'Settings'),
          ],
        ),
      ),
      body: SafeArea(
        top: false,
        child: setupNeeded
            ? SetupPage(session: widget.session, onComplete: () => setState(() => setupNeeded = false))
            : IndexedStack(index: page, children: _pages),
      ),
    );
  }

  Widget _nav(int index, IconData icon, String label) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final selected = page == index;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 5),
      child: Container(
        decoration: BoxDecoration(
          color: selected
              ? (isDark ? const Color(0x26f97316) : const Color(0xfffff1e9))
              : (isDark ? const Color(0xff0d1c2e) : const Color(0xfff7f9fc)),
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: selected ? const Color(0x99f97316) : (isDark ? const Color(0x334a74a9) : const Color(0xffe2e8f0))),
        ),
        child: ListTile(
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
          leading: Icon(icon, color: selected ? const Color(0xfff97316) : (isDark ? const Color(0xff9fb4d0) : const Color(0xff52708f))),
          title: Text(label, style: TextStyle(color: selected ? const Color(0xffe95f17) : (isDark ? const Color(0xffedf5ff) : const Color(0xff10233d)), fontWeight: selected ? FontWeight.w700 : FontWeight.w600)),
          trailing: Icon(Icons.chevron_right_rounded, color: selected ? const Color(0xfff97316) : (isDark ? const Color(0xff6f88aa) : const Color(0xff94a3b8))),
          onTap: () {
            Navigator.pop(context);
            setState(() => page = index);
          },
        ),
      ),
    );
  }
}
class SetupPage extends StatefulWidget {
  const SetupPage({super.key, required this.session, required this.onComplete});
  final Session session;
  final VoidCallback onComplete;
  @override
  State<SetupPage> createState() => _SetupPageState();
}

class _SetupPageState extends State<SetupPage> {
  String mode = 'paper';
  bool saving = false;
  String? error;
  final dhanId = TextEditingController(),
      dhanToken = TextEditingController(),
      aiKey = TextEditingController();

  @override
  void initState() {
    super.initState();
    final current = widget.session.botConfig;
    mode = current['trading_mode'] == 'live' ? 'live' : 'paper';
    dhanId.text = current['dhan_client_id'] ?? '';
    dhanToken.text = current['dhan_access_token'] ?? '';
    aiKey.text = current['ai_key'] ?? '';
  }

  @override
  void dispose() {
    dhanId.dispose();
    dhanToken.dispose();
    aiKey.dispose();
    super.dispose();
  }
  Future<void> save() async {
    setState(() {
      saving = true;
      error = null;
    });
    try {
      if (mode == 'live' && (dhanId.text.trim().isEmpty || dhanToken.text.trim().isEmpty)) {
        throw ApiError('Dhan client ID and access token are required for live trading.');
      }
      await widget.session.api.saveConfig({
        'trading_mode': mode,
        'dhan_client_id': dhanId.text.trim(),
        'dhan_access_token': dhanToken.text.trim(),
        'ai_key': aiKey.text.trim(),
        'council_mode': aiKey.text.trim().isEmpty ? 'local' : 'dual',
      });
      widget.onComplete();
    } on ApiError catch (e) {
      if (mounted) setState(() => error = e.message);
    } finally {
      if (mounted) setState(() => saving = false);
    }
  }

  @override
  Widget build(BuildContext context) => ListView(
    padding: const EdgeInsets.all(20),
    children: [
      Text(
        'First-time bot setup',
        style: Theme.of(context).textTheme.headlineSmall,
      ),
      const SizedBox(height: 8),
      const Text(
        'Credentials stay on this device and are supplied to the local Python engine only when MaxAlpha runs.',
      ),
      const SizedBox(height: 20),
      SegmentedButton<String>(
        segments: const [
          ButtonSegment(value: 'paper', label: Text('Paper trading')),
          ButtonSegment(value: 'live', label: Text('Live trading')),
        ],
        selected: {mode},
        onSelectionChanged: (s) => setState(() => mode = s.first),
      ),
      const SizedBox(height: 16),
      TextField(
        controller: dhanId,
        decoration: InputDecoration(
          labelText:
              'Dhan client ID ${mode == 'live' ? '(required)' : '(optional)'}',
        ),
      ),
      const SizedBox(height: 12),
      TextField(
        controller: dhanToken,
        obscureText: true,
        decoration: InputDecoration(
          labelText:
              'Dhan access token ${mode == 'live' ? '(required)' : '(optional)'}',
        ),
      ),
      const SizedBox(height: 20),
      TextField(
        controller: aiKey,
        obscureText: true,
        decoration: const InputDecoration(
          labelText: 'AI API key (optional)',
          helperText:
              'Empty = local council. Supplied key = AI + local council.',
        ),
      ),
      if (error != null)
        Padding(
          padding: const EdgeInsets.only(top: 12),
          child: Text(error!, style: const TextStyle(color: Colors.red)),
        ),
      const SizedBox(height: 20),
      FilledButton(
        onPressed: saving ? null : save,
        child: const Text('Save configuration'),
      ),
    ],
  );
}

class DashboardPage extends StatefulWidget {
  const DashboardPage({super.key, required this.session});
  final Session session;
  @override
  State<DashboardPage> createState() => _DashboardPageState();
}

class _DashboardPageState extends State<DashboardPage> {
  Map<String, dynamic> data = {};
  bool loading = true;
  Timer? _autoRefreshTimer;
  int _activeSubTab = 0; // 0: P&L Breakdown, 1: Positions, 2: Cycle History, 3: Orders

  @override
  void initState() {
    super.initState();
    refresh();
    // Auto refresh every 3 seconds so live bot cycles show up dynamically
    _autoRefreshTimer = Timer.periodic(const Duration(seconds: 3), (_) => refresh());
  }

  @override
  void dispose() {
    _autoRefreshTimer?.cancel();
    super.dispose();
  }

  Future<void> refresh() async {
    try {
      final res = await widget.session.api.dashboard();
      final logs = await widget.session.api.logs();
      res['running'] = logs['running'] == true;
      if (mounted) {
        setState(() {
          data = res;
          loading = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() => loading = false);
    }
  }

  Future<void> _toggleBotStatus() async {
    final isRunning = data['running'] == true;
    if (isRunning) {
      final confirm = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: const Text('Stop MaxAlpha Bot?'),
          content: const Text('The bot will safely complete its current cycle and stop trading.'),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
            FilledButton(
              style: FilledButton.styleFrom(backgroundColor: const Color(0xffef4444)),
              onPressed: () => Navigator.pop(ctx, true),
              child: const Text('Stop Engine'),
            ),
          ],
        ),
      );
      if (confirm == true) {
        await widget.session.api.stop();
        await refresh();
      }
    } else {
      final input = TextEditingController();
      final raw = await showDialog<String?>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: const Text('Start MaxAlpha Engine'),
          content: TextField(
            controller: input,
            keyboardType: TextInputType.number,
            decoration: const InputDecoration(
              hintText: 'Wallet budget (leave empty for default ₹50,000)',
              labelText: 'Trading Budget (₹)',
            ),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
            FilledButton(
              style: FilledButton.styleFrom(backgroundColor: const Color(0xff22c55e)),
              onPressed: () => Navigator.pop(ctx, input.text),
              child: const Text('Start Trading'),
            ),
          ],
        ),
      );
      if (raw != null) {
        final amount = raw.trim().isEmpty ? null : double.tryParse(raw.replaceAll(',', ''));
        await widget.session.api.start(amount);
        await refresh();
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final historyList = (data['history'] as List?) ?? [];
    final positionsList = (data['positions_detail'] as List?) ?? [];
    final tradeStats = (data['trade_stats'] as Map<String, dynamic>?) ?? {};
    final ordersList = (tradeStats['order_history'] as List?) ?? (tradeStats['closed_trades'] as List?) ?? [];
    final isRunning = data['running'] == true;
    final regimeStr = (data['regime'] as String?) ?? 'NEUTRAL';
    final regimeScore = num.tryParse(data['regime_score']?.toString() ?? '0.5') ?? 0.5;
    
    final totalVal = num.tryParse(data['total']?.toString() ?? '0') ?? 0;
    final cashVal = num.tryParse(data['cash']?.toString() ?? '0') ?? 0;
    final investedVal = num.tryParse(data['invested']?.toString() ?? '0') ?? (totalVal - cashVal > 0 ? totalVal - cashVal : 0);

    final t1 = num.tryParse(data['tier1_usd']?.toString() ?? '0') ?? 0;
    final t2 = num.tryParse(data['tier2_usd']?.toString() ?? '0') ?? 0;
    final t3 = num.tryParse(data['tier3_usd']?.toString() ?? '0') ?? 0;

    // Calculate session start value from history or total
    final startVal = historyList.isNotEmpty
        ? (num.tryParse(historyList.first['wallet_cap']?.toString() ?? historyList.first['total_usd']?.toString() ?? totalVal.toString()) ?? totalVal)
        : totalVal;
    final sessionPnl = totalVal > 0 && startVal > 0 ? totalVal - startVal : 0.0;
    final sessionPct = startVal > 0 ? (sessionPnl / startVal * 100) : 0.0;

    // Calculate unrealized P&L and profitable sellers
    num openPnl = 0;
    int winnersCount = 0;
    for (final pos in positionsList) {
      final p = pos as Map<String, dynamic>;
      final bp = num.tryParse(p['buy_price']?.toString() ?? p['entry_price']?.toString() ?? '0') ?? 0;
      final cp = num.tryParse(p['current_price']?.toString() ?? bp.toString()) ?? bp;
      final qty = num.tryParse(p['qty']?.toString() ?? '1') ?? 1;
      final pnl = (cp - bp) * qty;
      openPnl += pnl;
      if (pnl > 0) winnersCount++;
    }

    // Win rate calculations
    final wins = num.tryParse(tradeStats['wins']?.toString() ?? '0')?.toInt() ?? 0;
    final losses = num.tryParse(tradeStats['losses']?.toString() ?? '0')?.toInt() ?? 0;
    final totalTrades = wins + losses;
    final winRatePct = totalTrades > 0 ? (wins / totalTrades * 100) : 0.0;

    return Scaffold(
      backgroundColor: isDark ? const Color(0xff07111f) : const Color(0xfff5f7fb),
      floatingActionButton: FloatingActionButton.extended(
        backgroundColor: isRunning ? const Color(0xffef4444) : const Color(0xfff97316),
        foregroundColor: Colors.white,
        elevation: 6,
        onPressed: _toggleBotStatus,
        icon: Icon(isRunning ? Icons.stop_circle_rounded : Icons.play_arrow_rounded),
        label: Text(
          isRunning ? 'STOP BOT' : 'RUN BOT',
          style: const TextStyle(fontWeight: FontWeight.bold, letterSpacing: 1),
        ),
      ),
      body: RefreshIndicator(
        onRefresh: refresh,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            // ── HERO ENGINE STATUS & SESSION BANNER ────────────────────────
            Container(
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  colors: isRunning
                      ? [const Color(0xff0f2b1d), const Color(0xff0b1729)]
                      : [const Color(0xff1e1a2b), const Color(0xff0b1729)],
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                ),
                borderRadius: BorderRadius.circular(20),
                border: Border.all(
                  color: isRunning
                      ? const Color(0xff22c55e).withValues(alpha: 0.35)
                      : const Color(0xfff97316).withValues(alpha: 0.35),
                  width: 1.5,
                ),
                boxShadow: [
                  BoxShadow(
                    color: (isRunning ? const Color(0xff22c55e) : const Color(0xfff97316)).withValues(alpha: 0.12),
                    blurRadius: 16,
                    spreadRadius: 2,
                  ),
                ],
              ),
              padding: const EdgeInsets.all(18),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Row(
                        children: [
                          Container(
                            width: 10,
                            height: 10,
                            decoration: BoxDecoration(
                              color: isRunning ? const Color(0xff22c55e) : const Color(0xff94a3b8),
                              shape: BoxShape.circle,
                              boxShadow: isRunning
                                  ? [const BoxShadow(color: Color(0xff22c55e), blurRadius: 8, spreadRadius: 2)]
                                  : null,
                            ),
                          ),
                          const SizedBox(width: 8),
                          Text(
                            isRunning ? 'LIVE ENGINE ACTIVE' : 'ENGINE STANDBY',
                            style: TextStyle(
                              color: isRunning ? const Color(0xff22c55e) : const Color(0xff94a3b8),
                              fontFamily: 'monospace',
                              fontWeight: FontWeight.bold,
                              fontSize: 12,
                              letterSpacing: 1.1,
                            ),
                          ),
                        ],
                      ),
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                        decoration: BoxDecoration(
                          color: const Color(0x203b82f6),
                          borderRadius: BorderRadius.circular(8),
                          border: Border.all(color: const Color(0x403b82f6)),
                        ),
                        child: Text(
                          'CYCLE #${historyList.isNotEmpty ? (historyList.last['cycle'] ?? historyList.length) : 0}',
                          style: const TextStyle(
                            fontFamily: 'monospace',
                            color: Color(0xff3b82f6),
                            fontSize: 11,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 16),
                  const Text(
                    'PORTFOLIO VALUE',
                    style: TextStyle(color: Color(0xff6f88aa), fontSize: 10, letterSpacing: 1.2, fontWeight: FontWeight.bold),
                  ),
                  const SizedBox(height: 4),
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.baseline,
                    textBaseline: TextBaseline.alphabetic,
                    children: [
                      Text(
                        '₹${totalVal.toStringAsFixed(2)}',
                        style: TextStyle(
                          color: isDark ? Colors.white : const Color(0xff10233d),
                          fontFamily: 'monospace',
                          fontSize: 28,
                          fontWeight: FontWeight.bold,
                          letterSpacing: -.5,
                        ),
                      ),
                      const SizedBox(width: 12),
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                        decoration: BoxDecoration(
                          color: sessionPnl >= 0 ? const Color(0x2022c55e) : const Color(0x20ef4444),
                          borderRadius: BorderRadius.circular(6),
                        ),
                        child: Text(
                          '${sessionPnl >= 0 ? '+' : ''}₹${sessionPnl.abs().toStringAsFixed(2)} (${sessionPct >= 0 ? '+' : ''}${sessionPct.toStringAsFixed(2)}%)',
                          style: TextStyle(
                            color: sessionPnl >= 0 ? const Color(0xff22c55e) : const Color(0xffef4444),
                            fontFamily: 'monospace',
                            fontWeight: FontWeight.bold,
                            fontSize: 12,
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: isDark ? const Color(0x4007111f) : const Color(0xfff1f5f9),
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(color: isDark ? const Color(0x201481b9) : const Color(0xffcbd5e1)),
                    ),
                    child: Text(
                      positionsList.isNotEmpty
                          ? 'Session started with ₹${startVal.toStringAsFixed(2)}. $winnersCount of ${positionsList.length} open stocks can be sold above buy price right now.'
                          : 'No open stock positions. MaxAlpha scans for gap-ups and tier signals during NSE market hours.',
                      style: TextStyle(color: isDark ? const Color(0xffedf5ff) : const Color(0xff334155), fontSize: 11.5, height: 1.4),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 14),

            // ── MARKET REGIME & ALLOCATION CAROUSEL/BANNER ───────────────────
            _buildRegimeBanner(regimeStr, regimeScore, t1, t2, t3, totalVal, isDark),
            const SizedBox(height: 14),

            // ── METRIC CARDS GRID ──────────────────────────────────────────
            GridView.count(
              crossAxisCount: 2,
              childAspectRatio: 1.55,
              shrinkWrap: true,
              physics: const NeverScrollableScrollPhysics(),
              crossAxisSpacing: 10,
              mainAxisSpacing: 10,
              children: [
                _buildMetricCard('CASH AVAILABLE', '₹${cashVal.toStringAsFixed(0)}', 'Deployable budget', const Color(0xff38bdf8), isDark),
                _buildMetricCard('INVESTED', '₹${investedVal.toStringAsFixed(0)}', '${positionsList.length} active stocks', const Color(0xfffb923c), isDark),
                _buildMetricCard('UNREALIZED P&L', '${openPnl >= 0 ? '+' : ''}₹${openPnl.abs().toStringAsFixed(2)}', openPnl >= 0 ? 'Profitable positions' : 'Open position drawdown', openPnl >= 0 ? const Color(0xff22c55e) : const Color(0xffef4444), isDark),
                _buildMetricCard('WIN RATE', totalTrades > 0 ? '${winRatePct.toStringAsFixed(1)}%' : 'N/A', '$wins wins / $totalTrades total trades', winRatePct >= 50 ? const Color(0xff22c55e) : const Color(0xfff97316), isDark),
              ],
            ),
            const SizedBox(height: 16),

            // ── SUB-SECTION TAB SELECTOR ──────────────────────────────────
            Container(
              height: 44,
              decoration: BoxDecoration(
                color: isDark ? const Color(0xff10233d) : const Color(0xffe2e8f0),
                borderRadius: BorderRadius.circular(14),
              ),
              padding: const EdgeInsets.all(3),
              child: Row(
                children: [
                  _subTabButton(0, 'P&L Breakdown'),
                  _subTabButton(1, 'Positions (${positionsList.length})'),
                  _subTabButton(2, 'Cycles (${historyList.length})'),
                  _subTabButton(3, 'Orders (${ordersList.length})'),
                ],
              ),
            ),
            const SizedBox(height: 12),

            // ── SUB-SECTION CONTENT ───────────────────────────────────────
            if (_activeSubTab == 0) _buildScrollablePnlBreakdown(historyList, startVal, isDark),
            if (_activeSubTab == 1) _buildPositionsSection(positionsList, isDark),
            if (_activeSubTab == 2) _buildCyclesSection(historyList, isDark),
            if (_activeSubTab == 3) _buildOrdersSection(ordersList, isDark),

            const SizedBox(height: 16),

            // ── TIER ALLOCATION CARDS ───────────────────────────────────────
            const Text(
              'TIER STRATEGY ALLOCATION',
              style: TextStyle(color: Color(0xff6f88aa), fontSize: 10, letterSpacing: 1.2, fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 8),
            _tierDetailCard('Tier 1 — Momentum Smallcaps', 'Target: Rs 20 – Rs 250 • 60% Bull Alloc', t1, totalVal, const Color(0xfffb923c), isDark),
            const SizedBox(height: 8),
            _tierDetailCard('Tier 2 — Midcap Growth', 'Target: Rs 250 – Rs 1,500 • 30% Bull Alloc', t2, totalVal, const Color(0xff3b82f6), isDark),
            const SizedBox(height: 8),
            _tierDetailCard('Tier 3 — Bluechip / ETF', 'Target: Core Safety • 10% Bull Alloc', t3, totalVal, const Color(0xff22c55e), isDark),

            const SizedBox(height: 24),
          ],
        ),
      ),
    );
  }

  Widget _subTabButton(int index, String label) {
    final selected = _activeSubTab == index;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return Expanded(
      child: GestureDetector(
        onTap: () => setState(() => _activeSubTab = index),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 200),
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: selected
                ? (isDark ? const Color(0xff07111f) : Colors.white)
                : Colors.transparent,
            borderRadius: BorderRadius.circular(11),
            boxShadow: selected
                ? [BoxShadow(color: Colors.black.withValues(alpha: 0.1), blurRadius: 4)]
                : null,
          ),
          child: Text(
            label,
            style: TextStyle(
              color: selected
                  ? const Color(0xfff97316)
                  : (isDark ? const Color(0xff6f88aa) : const Color(0xff64748b)),
              fontSize: 11,
              fontWeight: selected ? FontWeight.bold : FontWeight.w600,
            ),
          ),
        ),
      ),
    );
  }

  // ── 1. SCROLLABLE P&L BREAKDOWN TABLE (User requested max height scrollable container!) ──
  Widget _buildScrollablePnlBreakdown(List historyList, num sessionStartValue, bool isDark) {
    if (historyList.isEmpty) {
      return Container(
        height: 140,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: isDark ? const Color(0xff10233d) : Colors.white,
          borderRadius: BorderRadius.circular(16),
        ),
        child: const Text('No cycle data yet. Start bot to generate live P&L history.', style: TextStyle(color: Color(0xff6f88aa), fontSize: 12)),
      );
    }

    return Container(
      decoration: BoxDecoration(
        color: isDark ? const Color(0xff10233d) : Colors.white,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: isDark ? const Color(0x284a74a9) : const Color(0xffe2e8f0)),
      ),
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              const Text('P&L BREAKDOWN (PER CYCLE)', style: TextStyle(color: Color(0xff6f88aa), fontSize: 10, fontWeight: FontWeight.bold, letterSpacing: 1.1)),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(color: const Color(0x20f97316), borderRadius: BorderRadius.circular(6)),
                child: Text('${historyList.length} CYCLES', style: const TextStyle(color: Color(0xfff97316), fontSize: 10, fontWeight: FontWeight.bold, fontFamily: 'monospace')),
              ),
            ],
          ),
          const SizedBox(height: 10),
          // FIXED HEIGHT SCROLLABLE CONTAINER (As explicitly requested by user!)
          SizedBox(
            height: 300,
            child: Scrollbar(
              thumbVisibility: true,
              child: ListView.separated(
                itemCount: historyList.length,
                separatorBuilder: (ctx, idx) => Divider(height: 1, color: isDark ? const Color(0x204a74a9) : const Color(0xfff1f5f9)),
                itemBuilder: (ctx, idx) {
                  // Latest cycle at top
                  final item = (historyList.reversed.toList())[idx] as Map<String, dynamic>;
                  final cycleNum = item['cycle'] ?? (historyList.length - idx);
                  final totalUsd = num.tryParse(item['total_usd']?.toString() ?? '0') ?? 0;
                  
                  // Compute cycle P&L vs previous cycle
                  num prevVal = sessionStartValue;
                  final origIdx = historyList.length - 1 - idx;
                  if (origIdx > 0) {
                    final prevItem = historyList[origIdx - 1] as Map<String, dynamic>;
                    prevVal = num.tryParse(prevItem['total_usd']?.toString() ?? '0') ?? sessionStartValue;
                  }
                  final pnl = totalUsd - prevVal;
                  final pct = prevVal > 0 ? (pnl / prevVal * 100) : 0.0;
                  final isPos = pnl >= 0;

                  return Padding(
                    padding: const EdgeInsets.symmetric(vertical: 8, horizontal: 4),
                    child: Row(
                      children: [
                        SizedBox(
                          width: 70,
                          child: Text(
                            'Cycle $cycleNum',
                            style: TextStyle(
                              color: isDark ? const Color(0xff9fb4d0) : const Color(0xff64748b),
                              fontSize: 11,
                              fontFamily: 'monospace',
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                        ),
                        Expanded(
                          flex: 3,
                          child: Text(
                            '${isPos ? '+' : ''}₹${pnl.abs().toStringAsFixed(2)}',
                            style: TextStyle(
                              color: isPos ? const Color(0xff22c55e) : const Color(0xffef4444),
                              fontFamily: 'monospace',
                              fontWeight: FontWeight.bold,
                              fontSize: 12,
                            ),
                          ),
                        ),
                        Expanded(
                          flex: 3,
                          child: Text(
                            '₹${totalUsd.toStringAsFixed(0)}',
                            style: TextStyle(
                              color: isDark ? const Color(0xffedf5ff) : const Color(0xff10233d),
                              fontFamily: 'monospace',
                              fontSize: 11.5,
                            ),
                          ),
                        ),
                        SizedBox(
                          width: 60,
                          child: Container(
                            height: 4,
                            decoration: BoxDecoration(
                              color: isDark ? const Color(0xff0b1729) : const Color(0xffe2e8f0),
                              borderRadius: BorderRadius.circular(2),
                            ),
                            alignment: Alignment.centerLeft,
                            child: FractionallySizedBox(
                              widthFactor: (pct.abs() / 2.0).clamp(0.05, 1.0),
                              child: Container(
                                decoration: BoxDecoration(
                                  color: isPos ? const Color(0xff22c55e) : const Color(0xffef4444),
                                  borderRadius: BorderRadius.circular(2),
                                ),
                              ),
                            ),
                          ),
                        ),
                        const SizedBox(width: 8),
                        SizedBox(
                          width: 55,
                          child: Text(
                            '${isPos ? '+' : ''}${pct.toStringAsFixed(2)}%',
                            textAlign: TextAlign.end,
                            style: TextStyle(
                              color: isPos ? const Color(0xff22c55e) : const Color(0xffef4444),
                              fontSize: 10.5,
                              fontFamily: 'monospace',
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                        ),
                      ],
                    ),
                  );
                },
              ),
            ),
          ),
        ],
      ),
    );
  }

  // ── 2. POSITIONS SECTION ──
  Widget _buildPositionsSection(List positionsList, bool isDark) {
    if (positionsList.isEmpty) {
      return Container(
        height: 140,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: isDark ? const Color(0xff10233d) : Colors.white,
          borderRadius: BorderRadius.circular(16),
        ),
        child: const Text('No active positions right now.', style: TextStyle(color: Color(0xff6f88aa), fontSize: 12)),
      );
    }

    return Column(
      children: positionsList.map((pos) {
        final p = pos as Map<String, dynamic>;
        final sym = p['ticker']?.toString() ?? p['symbol']?.toString() ?? 'STOCK';
        final qty = p['qty'] ?? p['shares'] ?? 0;
        final buyPx = num.tryParse(p['buy_price']?.toString() ?? p['entry_price']?.toString() ?? '0') ?? 0;
        final curPx = num.tryParse(p['current_price']?.toString() ?? buyPx.toString()) ?? buyPx;
        final pnlPct = num.tryParse(p['pnl_pct']?.toString() ?? '0') ?? 0;
        final profitRs = (curPx - buyPx) * (num.tryParse(qty.toString()) ?? 1);
        final canSell = profitRs > 0;
        final winProb = num.tryParse(p['win_probability']?.toString() ?? '0') ?? 0;
        final tier = p['tier'] ?? 1;

        return Card(
          margin: const EdgeInsets.only(bottom: 10),
          color: isDark ? const Color(0xff10233d) : Colors.white,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(14),
            side: BorderSide(color: canSell ? const Color(0x4022c55e) : (isDark ? const Color(0x284a74a9) : const Color(0xffe2e8f0))),
          ),
          child: Padding(
            padding: const EdgeInsets.all(14),
            child: Column(
              children: [
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Row(
                      children: [
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                          decoration: BoxDecoration(
                            color: tier == 1 ? const Color(0x20fb923c) : tier == 2 ? const Color(0x203b82f6) : const Color(0x2022c55e),
                            borderRadius: BorderRadius.circular(6),
                          ),
                          child: Text('T$tier $sym', style: const TextStyle(fontFamily: 'monospace', fontWeight: FontWeight.bold, fontSize: 13)),
                        ),
                        const SizedBox(width: 8),
                        Text('Qty: $qty', style: const TextStyle(color: Color(0xff6f88aa), fontSize: 11)),
                      ],
                    ),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                      decoration: BoxDecoration(
                        color: canSell ? const Color(0x2022c55e) : const Color(0x20ef4444),
                        borderRadius: BorderRadius.circular(6),
                      ),
                      child: Text(
                        canSell ? 'PROFITABLE (SELL OK)' : 'HOLDING (RECOVERY)',
                        style: TextStyle(color: canSell ? const Color(0xff22c55e) : const Color(0xffef4444), fontSize: 9.5, fontWeight: FontWeight.bold),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Text('BUY PRICE', style: TextStyle(color: Color(0xff6f88aa), fontSize: 9.5)),
                        Text('₹${buyPx.toStringAsFixed(2)}', style: const TextStyle(fontFamily: 'monospace', fontWeight: FontWeight.w600, fontSize: 12)),
                      ],
                    ),
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Text('CURRENT PRICE', style: TextStyle(color: Color(0xff6f88aa), fontSize: 9.5)),
                        Text('₹${curPx.toStringAsFixed(2)}', style: const TextStyle(fontFamily: 'monospace', fontWeight: FontWeight.w600, fontSize: 12)),
                      ],
                    ),
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.end,
                      children: [
                        const Text('UNREALIZED P&L', style: TextStyle(color: Color(0xff6f88aa), fontSize: 9.5)),
                        Text(
                          '${profitRs >= 0 ? '+' : ''}₹${profitRs.abs().toStringAsFixed(2)} (${pnlPct >= 0 ? '+' : ''}${pnlPct.toStringAsFixed(2)}%)',
                          style: TextStyle(color: profitRs >= 0 ? const Color(0xff22c55e) : const Color(0xffef4444), fontFamily: 'monospace', fontWeight: FontWeight.bold, fontSize: 12),
                        ),
                      ],
                    ),
                  ],
                ),
                if (winProb > 0) ...[
                  const SizedBox(height: 8),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Text('Est. Win Chance: ${winProb.toStringAsFixed(1)}%', style: const TextStyle(color: Color(0xff6f88aa), fontSize: 10.5)),
                      ClipRRect(
                        borderRadius: BorderRadius.circular(4),
                        child: SizedBox(
                          width: 100,
                          child: LinearProgressIndicator(
                            value: (winProb / 100).clamp(0.0, 1.0),
                            color: winProb >= 50 ? const Color(0xff22c55e) : const Color(0xfff97316),
                            backgroundColor: isDark ? const Color(0xff0b1729) : const Color(0xffe2e8f0),
                            minHeight: 4,
                          ),
                        ),
                      ),
                    ],
                  ),
                ],
              ],
            ),
          ),
        );
      }).toList(),
    );
  }

  // ── 3. CYCLES SECTION ──
  Widget _buildCyclesSection(List historyList, bool isDark) {
    if (historyList.isEmpty) {
      return Container(
        height: 140,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: isDark ? const Color(0xff10233d) : Colors.white,
          borderRadius: BorderRadius.circular(16),
        ),
        child: const Text('No cycle logs available yet.', style: TextStyle(color: Color(0xff6f88aa), fontSize: 12)),
      );
    }

    return Container(
      decoration: BoxDecoration(
        color: isDark ? const Color(0xff10233d) : Colors.white,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: isDark ? const Color(0x284a74a9) : const Color(0xffe2e8f0)),
      ),
      padding: const EdgeInsets.all(12),
      child: SizedBox(
        height: 300,
        child: ListView.separated(
          itemCount: historyList.length,
          separatorBuilder: (ctx, idx) => Divider(height: 1, color: isDark ? const Color(0x204a74a9) : const Color(0xfff1f5f9)),
          itemBuilder: (ctx, idx) {
            final item = (historyList.reversed.toList())[idx] as Map<String, dynamic>;
            final cycle = item['cycle'] ?? (historyList.length - idx);
            final tot = num.tryParse(item['total_usd']?.toString() ?? '0') ?? 0;
            final reg = item['regime']?.toString() ?? 'NEUTRAL';
            final sigs = item['signals'] ?? 0;
            final ts = item['ts']?.toString() ?? '';
            String timeStr = '--';
            if (ts.isNotEmpty) {
              try {
                final dt = DateTime.parse(ts).toLocal();
                timeStr = '${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}';
              } catch (_) {}
            }

            return ListTile(
              dense: true,
              leading: Text('C#$cycle', style: const TextStyle(fontFamily: 'monospace', color: Color(0xfff97316), fontWeight: FontWeight.bold, fontSize: 11)),
              title: Text('Portfolio: ₹${tot.toStringAsFixed(0)}', style: const TextStyle(fontFamily: 'monospace', fontWeight: FontWeight.w600, fontSize: 12)),
              subtitle: Text('Regime: $reg • Time: $timeStr', style: const TextStyle(color: Color(0xff6f88aa), fontSize: 10)),
              trailing: Text('$sigs Signals', style: TextStyle(color: sigs > 0 ? const Color(0xff22c55e) : const Color(0xff6f88aa), fontSize: 10, fontWeight: FontWeight.bold)),
            );
          },
        ),
      ),
    );
  }

  // ── 4. ORDERS SECTION ──
  Widget _buildOrdersSection(List ordersList, bool isDark) {
    if (ordersList.isEmpty) {
      return Container(
        height: 140,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: isDark ? const Color(0xff10233d) : Colors.white,
          borderRadius: BorderRadius.circular(16),
        ),
        child: const Text('No orders or closed trades yet.', style: TextStyle(color: Color(0xff6f88aa), fontSize: 12)),
      );
    }

    return Container(
      decoration: BoxDecoration(
        color: isDark ? const Color(0xff10233d) : Colors.white,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: isDark ? const Color(0x284a74a9) : const Color(0xffe2e8f0)),
      ),
      padding: const EdgeInsets.all(12),
      child: SizedBox(
        height: 300,
        child: ListView.separated(
          itemCount: ordersList.length,
          separatorBuilder: (ctx, idx) => Divider(height: 1, color: isDark ? const Color(0x204a74a9) : const Color(0xfff1f5f9)),
          itemBuilder: (ctx, idx) {
            final order = (ordersList.reversed.toList())[idx] as Map<String, dynamic>;
            final side = (order['event']?.toString() ?? (order['sell_price'] != null ? 'SELL' : 'BUY')).toUpperCase();
            final tkr = order['ticker']?.toString() ?? '--';
            final qty = order['qty'] ?? 0;
            final px = num.tryParse(order['price']?.toString() ?? order['buy_price']?.toString() ?? '0') ?? 0;
            final pnl = num.tryParse(order['pnl']?.toString() ?? '0') ?? 0;

            return ListTile(
              dense: true,
              leading: Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  color: side == 'BUY' ? const Color(0x203b82f6) : const Color(0x2022c55e),
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Text(side, style: TextStyle(color: side == 'BUY' ? const Color(0xff3b82f6) : const Color(0xff22c55e), fontSize: 10, fontWeight: FontWeight.bold)),
              ),
              title: Text('$tkr (Qty: $qty @ ₹${px.toStringAsFixed(1)})', style: const TextStyle(fontFamily: 'monospace', fontWeight: FontWeight.w600, fontSize: 12)),
              subtitle: Text(order['reason']?.toString() ?? order['strategy']?.toString() ?? 'Cycle signal executed', style: const TextStyle(color: Color(0xff6f88aa), fontSize: 10)),
              trailing: side == 'SELL'
                  ? Text('${pnl >= 0 ? '+' : ''}₹${pnl.abs().toStringAsFixed(2)}', style: TextStyle(color: pnl >= 0 ? const Color(0xff22c55e) : const Color(0xffef4444), fontFamily: 'monospace', fontWeight: FontWeight.bold, fontSize: 11))
                  : null,
            );
          },
        ),
      ),
    );
  }

  // ── HELPER WIDGETS ──
  Widget _buildRegimeBanner(String regime, num score, num t1, num t2, num t3, num total, bool isDark) {
    Color bg, fg;
    final u = regime.toUpperCase();
    if (u.contains('BULL')) {
      bg = const Color(0x2022c55e);
      fg = const Color(0xff22c55e);
    } else if (u.contains('BEAR')) {
      bg = const Color(0x20ef4444);
      fg = const Color(0xffef4444);
    } else {
      bg = const Color(0x203b82f6);
      fg = const Color(0xff3b82f6);
    }

    return Container(
      decoration: BoxDecoration(
        color: isDark ? const Color(0xff0b1729) : Colors.white,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: fg.withValues(alpha: 0.3)),
      ),
      padding: const EdgeInsets.all(14),
      child: Column(
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Row(
                children: [
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                    decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(6)),
                    child: Text(u, style: TextStyle(color: fg, fontFamily: 'monospace', fontWeight: FontWeight.bold, fontSize: 12)),
                  ),
                  const SizedBox(width: 10),
                  Text('${(score * 100).toStringAsFixed(0)}% Score', style: const TextStyle(color: Color(0xff6f88aa), fontSize: 11, fontWeight: FontWeight.w600)),
                ],
              ),
              const Text('NIFTY Uptrend • RSI 64', style: TextStyle(color: Color(0xff6f88aa), fontSize: 10.5)),
            ],
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(child: _tierProgress('T1 Smallcap', t1, total, const Color(0xfffb923c), 0.60)),
              const SizedBox(width: 8),
              Expanded(child: _tierProgress('T2 Midcap', t2, total, const Color(0xff3b82f6), 0.30)),
              const SizedBox(width: 8),
              Expanded(child: _tierProgress('T3 Bluechip', t3, total, const Color(0xff22c55e), 0.10)),
            ],
          ),
        ],
      ),
    );
  }

  Widget _tierProgress(String label, num val, num total, Color color, double targetPct) {
    final pct = total > 0 ? (val / total).clamp(0.0, 1.0) : 0.0;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(label, style: const TextStyle(color: Color(0xff6f88aa), fontSize: 9.5)),
            Text('${(targetPct * 100).toStringAsFixed(0)}%', style: TextStyle(color: color, fontSize: 9.5, fontWeight: FontWeight.bold)),
          ],
        ),
        const SizedBox(height: 4),
        ClipRRect(
          borderRadius: BorderRadius.circular(3),
          child: LinearProgressIndicator(
            value: pct.toDouble(),
            color: color,
            backgroundColor: const Color(0x20ffffff),
            minHeight: 5,
          ),
        ),
      ],
    );
  }

  Widget _buildMetricCard(String label, String value, String sub, Color accent, bool isDark) {
    return Container(
      decoration: BoxDecoration(
        color: isDark ? const Color(0xff10233d) : Colors.white,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: isDark ? const Color(0x284a74a9) : const Color(0xffe2e8f0)),
      ),
      padding: const EdgeInsets.all(12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Text(label, style: const TextStyle(color: Color(0xff6f88aa), fontSize: 9.5, fontWeight: FontWeight.bold, letterSpacing: 1)),
          const SizedBox(height: 4),
          Text(value, style: TextStyle(color: accent, fontFamily: 'monospace', fontSize: 16, fontWeight: FontWeight.bold)),
          const SizedBox(height: 2),
          Text(sub, style: TextStyle(color: isDark ? const Color(0xff9fb4d0) : const Color(0xff64748b), fontSize: 9.5), maxLines: 1, overflow: TextOverflow.ellipsis),
        ],
      ),
    );
  }

  Widget _tierDetailCard(String title, String target, num val, num total, Color color, bool isDark) {
    final pct = total > 0 ? (val / total * 100) : 0.0;
    return Container(
      decoration: BoxDecoration(
        color: isDark ? const Color(0xff10233d) : Colors.white,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: isDark ? const Color(0x284a74a9) : const Color(0xffe2e8f0)),
      ),
      padding: const EdgeInsets.all(12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(title, style: TextStyle(color: isDark ? Colors.white : const Color(0xff10233d), fontWeight: FontWeight.w600, fontSize: 12)),
              Text('₹${val.toStringAsFixed(0)} (${pct.toStringAsFixed(1)}%)', style: TextStyle(color: color, fontFamily: 'monospace', fontWeight: FontWeight.bold, fontSize: 12)),
            ],
          ),
          const SizedBox(height: 3),
          Text(target, style: const TextStyle(color: Color(0xff6f88aa), fontSize: 10)),
          const SizedBox(height: 6),
          ClipRRect(
            borderRadius: BorderRadius.circular(3),
            child: LinearProgressIndicator(
              value: (pct / 100).clamp(0.0, 1.0),
              color: color,
              backgroundColor: isDark ? const Color(0xff0b1729) : const Color(0xffe2e8f0),
              minHeight: 5,
            ),
          ),
        ],
      ),
    );
  }
}

class ExactDashboardPage extends StatefulWidget {
  const ExactDashboardPage({super.key, required this.session});
  final Session session;
  @override
  State<ExactDashboardPage> createState() => _ExactDashboardPageState();
}

class _ExactDashboardPageState extends State<ExactDashboardPage> {
  late final WebViewController _controller;
  Timer? _dashboardTimer;
  bool? _appliedDarkTheme;
  String? _loadedDashboardUrl;
  bool _dashboardLoaded = false;

  @override
  void initState() {
    super.initState();
    _controller = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setNavigationDelegate(NavigationDelegate(
        onNavigationRequest: (request) => request.url.startsWith('http://127.0.0.1:8765/')
            ? NavigationDecision.navigate
            : NavigationDecision.prevent,
        onPageFinished: (_) => _openDashboard(),
      ));
    widget.session.addListener(_loadDashboardWhenTargetChanges);
    _loadDashboard();
    _dashboardTimer = Timer.periodic(const Duration(seconds: 5), (_) => _syncHistory());
  }

  void _loadDashboardWhenTargetChanges() {
    if (!_dashboardLoaded || _loadedDashboardUrl != widget.session.dashboardUrl) _loadDashboard();
  }

  Future<void> _loadDashboard() async {
    final target = widget.session.dashboardUrl;
    if (_dashboardLoaded && _loadedDashboardUrl == target) return;
    _dashboardLoaded = true;
    _loadedDashboardUrl = target;
    if (target == null) {
      await _controller.loadFlutterAsset('assets/dashboard_v5_bot2.html');
    } else {
      await _controller.loadRequest(Uri.parse(target));
    }
  }

  Future<void> _openDashboard() async {
    try {
      // When served via the local HTTP server the HTML fetches performance_v4.json
      // itself — we only need to dismiss the login screen and boot the dashboard.
      // When loaded from a Flutter asset we also inject history so the file://
      // origin can display data without network access.
      final isHttpMode = _loadedDashboardUrl != null;
      final history = isHttpMode
          ? 'null'
          : jsonEncode((await widget.session.api.dashboard())['history'] ?? const []);
      await _controller.runJavaScript('''
        (function() {
          const dashboardHeader = document.querySelector('.hdr');
          if (dashboardHeader) dashboardHeader.style.display = 'none';
          // Flutter owns authentication — bypass the HTML login screen.
          const loginScreen = document.getElementById('ls');
          const app = document.getElementById('app');
          if (loginScreen) loginScreen.style.display = 'none';
          if (app) app.style.display = 'block';
          // Only inject history when NOT using HTTP server (HTTP mode reads
          // performance_v4.json directly, giving always-fresh data).
          if ($history !== null) window.__maxAlphaHistory = $history;
          if (!window.__maxAlphaFlutterBooted) {
            window.__maxAlphaFlutterBooted = true;
            if (typeof boot === 'function') boot();
          } else if (typeof loadAll === 'function') {
            loadAll();
            if (typeof updateAgentStatus === 'function') updateAgentStatus();
          }
        })();
      ''');
      if (!mounted) return;
      await _applyDashboardTheme(Theme.of(context).brightness == Brightness.dark);
    } catch (_) {}
  }

  Future<void> _applyDashboardTheme(bool dark) async {
    _appliedDarkTheme = dark;
    final values = dark
        ? '--bg:#07111f;--s1:#0b1729;--s2:#10233d;--s3:#15365f;--b1:rgba(148,185,255,.09);--b2:rgba(148,185,255,.15);--tx:#edf5ff;--mu:#6f88aa;--mu2:#9fb4d0;'
        : '--bg:#f4f7fb;--s1:#ffffff;--s2:#edf3fa;--s3:#e1ebf6;--b1:rgba(31,66,102,.12);--b2:rgba(31,66,102,.20);--tx:#10233d;--mu:#60758c;--mu2:#405873;';
    await _controller.runJavaScript("document.documentElement.style.cssText += '$values';");
  }

  Future<void> _syncHistory() async {
    try {
      // HTTP server mode: the dashboard fetches /performance_v4.json directly,
      // so a JS reload is sufficient — no need to inject data from Dart.
      // Asset (file://) mode: inject fresh history so the WebView can display it.
      final isHttpMode = _loadedDashboardUrl != null;
      if (isHttpMode) {
        // Tell the page to re-read the JSON from the server
        await _controller.runJavaScript(
          'if (document.getElementById("app") && document.getElementById("app").style.display === "block") { loadAll(); if(typeof updateAgentStatus==="function") updateAgentStatus(); }'
        );
      } else {
        final snapshot = await widget.session.api.dashboard();
        await _controller.runJavaScript(
          'window.__maxAlphaHistory = ${jsonEncode(snapshot["history"] ?? const [])}; '
          'if (document.getElementById("app") && document.getElementById("app").style.display === "block") { loadAll(); if(typeof updateAgentStatus==="function") updateAgentStatus(); }'
        );
      }
    } catch (_) {}
  }

  @override
  void dispose() {
    _dashboardTimer?.cancel();
    widget.session.removeListener(_loadDashboardWhenTargetChanges);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    if (_appliedDarkTheme != dark) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _applyDashboardTheme(dark));
    }
    return WebViewWidget(controller: _controller);
  }
}

class RunPage extends StatefulWidget {
  const RunPage({super.key, required this.session});
  final Session session;
  @override
  State<RunPage> createState() => _RunPageState();
}

class _RunPageState extends State<RunPage> {
  List<String> lines = [];
  bool running = false, waiting = false, startingDashboard = false;
  Timer? timer;
  @override
  void initState() {
    super.initState();
    refresh();
    timer = Timer.periodic(const Duration(seconds: 3), (_) => refresh());
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  Future<void> refresh() async {
    try {
      final d = await widget.session.api.logs();
      if (mounted) {
        setState(() {
          lines = List<String>.from(d['lines'] ?? []);
          running = d['running'] == true;
        });
      }
    } catch (_) {}
  }

  Future<void> start() async {
    final input = TextEditingController();
    final raw = await showDialog<String?>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Total wallet amount'),
        content: TextField(
          controller: input,
          keyboardType: TextInputType.number,
          decoration: const InputDecoration(
            hintText: 'Leave blank to use remaining wallet cap',
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, input.text),
            child: const Text('Start'),
          ),
        ],
      ),
    );
    if (raw == null) return;
    final amount = raw.trim().isEmpty
        ? null
        : double.tryParse(raw.replaceAll(',', ''));
    if (raw.trim().isNotEmpty && amount == null) return;
    setState(() => waiting = true);
    try {
      await widget.session.api.start(amount);
      await refresh();
    } on ApiError catch (e) {
      _notice(e.message);
    } finally {
      if (mounted) setState(() => waiting = false);
    }
  }

  Future<void> stop() async {
    await widget.session.api.stop();
    await refresh();
  }

  Future<void> startDashboard() async {
    setState(() => startingDashboard = true);
    try {
      final response = await widget.session.api.startDashboard();
      // The gateway always binds this loopback-only address. Keep the fallback
      // so an Android method-codec response can never leave a running server
      // disconnected from the WebView.
      final url = response['url']?.toString().trim();
      final dashboardUrl = (url == null || url.isEmpty)
          ? 'http://127.0.0.1:8765/dashboard_v5_bot2.html'
          : url;
      widget.session.setDashboardUrl(dashboardUrl);
      _notice('Dashboard server started. Open Dashboard from the menu.');
    } on ApiError catch (e) {
      _notice(e.message);
    } finally {
      if (mounted) setState(() => startingDashboard = false);
    }
  }

  void _notice(String value) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(value)));
  }

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.all(16),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('Run MaxAlpha', style: Theme.of(context).textTheme.headlineSmall),
        const SizedBox(height: 10),
        FilledButton.icon(
          onPressed: waiting || running ? null : start,
          icon: const Icon(Icons.play_arrow_rounded),
          label: const Text('Start MaxAlpha'),
        ),
        const SizedBox(height: 10),
        Row(
          children: [
            Expanded(
              child: OutlinedButton.icon(
                onPressed: !running ? null : stop,
                icon: const Icon(Icons.stop_rounded),
                label: const Text('Stop'),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              flex: 2,
              child: OutlinedButton.icon(
                onPressed: startingDashboard ? null : startDashboard,
                icon: const Icon(Icons.dashboard_outlined),
                label: Text(startingDashboard ? 'Starting…' : 'Start dashboard'),
              ),
            ),
            const SizedBox(width: 4),
            IconButton(
              tooltip: 'Refresh run status',
              onPressed: refresh,
              icon: const Icon(Icons.refresh_rounded),
            ),
          ],
        ),
        const SizedBox(height: 14),
        Expanded(
          child: Container(
            width: double.infinity,
            color: Colors.black,
            padding: const EdgeInsets.all(12),
            child: ListView(
              children: lines.isEmpty
                  ? [
                      const SizedBox(
                        height: 260,
                        child: Center(
                          child: Column(mainAxisSize: MainAxisSize.min, children: [
                            Icon(Icons.terminal_rounded, color: Color(0xff3b82f6), size: 36),
                            SizedBox(height: 12),
                            Text('No activity yet', style: TextStyle(color: Colors.white, fontWeight: FontWeight.w700)),
                            SizedBox(height: 5),
                            Text('Start MaxAlpha to stream local Python logs here.', style: TextStyle(color: Colors.white60, fontFamily: 'monospace', fontSize: 11)),
                          ]),
                        ),
                      ),
                    ]
                  : lines
                        .map(
                          (line) => Text(
                            line,
                            style: const TextStyle(
                              color: Colors.white,
                              fontFamily: 'monospace',
                              fontSize: 11,
                            ),
                          ),
                        )
                        .toList(),
            ),
          ),
        ),
      ],
    ),
  );
}

class SettingsPage extends StatelessWidget {
  const SettingsPage({
    super.key,
    required this.session,
    required this.onLogout,
  });
  final Session session;
  final VoidCallback onLogout;

  Future<void> _signals(BuildContext context) async {
    try {
      final data = await session.api.signals();
      if (!context.mounted) return;
      await showDialog<void>(
        context: context,
        builder: (dialogContext) => AlertDialog(
          title: const Text('Paper signals'),
          content: SingleChildScrollView(
            child: SelectableText(
              data['content'].toString(),
              style: const TextStyle(fontFamily: 'monospace', fontSize: 11),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(dialogContext),
              child: const Text('Close'),
            ),
          ],
        ),
      );
    } on ApiError catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(error.message)));
      }
    }
  }

  @override
  Widget build(BuildContext context) => ListView(
    padding: const EdgeInsets.fromLTRB(20, 28, 20, 112),
    children: [
      Text('CONTROL ROOM', style: Theme.of(context).textTheme.headlineSmall?.copyWith(fontFamily: 'monospace', fontWeight: FontWeight.bold, letterSpacing: 1.1)),
      const SizedBox(height: 6),
      Text('Appearance, credentials, and local records', style: Theme.of(context).textTheme.bodyMedium?.copyWith(color: Theme.of(context).colorScheme.outline)),
      const SizedBox(height: 22),
      Card(child: SwitchListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 18, vertical: 8),
        title: const Text('Dark theme'),
        subtitle: const Text('Dark blue trading-console theme'),
        value: session.dark,
        onChanged: session.toggleTheme,
      )),
      const SizedBox(height: 10),
      Card(child: ListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 18, vertical: 8),
        leading: const Icon(Icons.tune),
        title: const Text('Edit bot credentials'),
        subtitle: const Text('Update paper/live mode, Dhan, or AI council key'),
        onTap: () {
          Navigator.push(
            context,
            MaterialPageRoute(
              builder: (_) => Scaffold(
                appBar: AppBar(title: const Text('Edit configuration')),
                body: SetupPage(
                  session: session,
                  onComplete: () => Navigator.pop(context),
                ),
              ),
            ),
          );
        },
      )),
      const SizedBox(height: 10),
      Card(child: ListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 18, vertical: 8),
        leading: const Icon(Icons.description_outlined),
        title: const Text('View signals JSON'),
        subtitle: const Text(
          'Read the paper signals recorded on this device',
        ),
        onTap: () => _signals(context),
      )),
      const SizedBox(height: 10),
      Card(child: ListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 18, vertical: 8),
        leading: const Icon(Icons.logout),
        title: const Text('Log out'),
        onTap: () async {
          await session.signOut();
          onLogout();
        },
      )),
    ],
  );
}
