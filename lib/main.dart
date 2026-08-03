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
    SeriousPython.run(
      appFileName: 'main_ios.py',
      // Pass the app module path so Python imports resolve correctly.
      // serious_python stages the agent/ dir at <resources>/app/.
    );
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
      builder: (_, __) => MaterialApp(
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
    if (mounted) setState(() {
      setupNeeded = !widget.session.configured;
      setupChecked = true;
    });
  }

  @override
  Widget build(BuildContext context) {
    if (!setupChecked)
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
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

  @override
  void initState() {
    super.initState();
    refresh();
    // Auto refresh every 5 seconds so live bot cycles show up dynamically
    _autoRefreshTimer = Timer.periodic(const Duration(seconds: 5), (_) => refresh());
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

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final historyList = (data['history'] as List?) ?? [];
    final positionsList = (data['positions_detail'] as List?) ?? [];
    final isRunning = data['running'] == true;
    final regimeStr = (data['regime'] as String?) ?? 'Awaiting first cycle';
    final totalVal = num.tryParse(data['total']?.toString() ?? '0') ?? 0;
    final cashVal = num.tryParse(data['cash']?.toString() ?? '0') ?? 0;
    final investedVal = num.tryParse(data['invested']?.toString() ?? '0') ?? (totalVal - cashVal > 0 ? totalVal - cashVal : 0);
    final posCount = data['positions'] ?? 0;

    final t1 = num.tryParse(data['tier1_usd']?.toString() ?? '0') ?? 0;
    final t2 = num.tryParse(data['tier2_usd']?.toString() ?? '0') ?? 0;
    final t3 = num.tryParse(data['tier3_usd']?.toString() ?? '0') ?? 0;

    return Container(
      color: isDark ? const Color(0xff07111f) : const Color(0xfff5f7fb),
      child: RefreshIndicator(
        onRefresh: refresh,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            // ── Live Engine Status Banner ──────────────────────────────────
            Card(
              color: isDark ? const Color(0xff10233d) : Colors.white,
              elevation: 0,
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(16),
                side: BorderSide(
                  color: isRunning
                      ? const Color(0xff22c55e).withOpacity(0.4)
                      : (isDark ? const Color(0x334a74a9) : const Color(0xffe2e8f0)),
                ),
              ),
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Row(
                  children: [
                    Container(
                      width: 44,
                      height: 44,
                      decoration: BoxDecoration(
                        color: isRunning
                            ? const Color(0x2022c55e)
                            : (isDark ? const Color(0x2094a3b8) : const Color(0xfff1f5f9)),
                        borderRadius: BorderRadius.circular(12),
                      ),
                      child: Icon(
                        isRunning ? Icons.play_arrow_rounded : Icons.pause_rounded,
                        color: isRunning ? const Color(0xff22c55e) : const Color(0xff94a3b8),
                        size: 26,
                      ),
                    ),
                    const SizedBox(width: 14),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Row(
                            children: [
                              Text(
                                isRunning ? 'MAX ALPHA RUNNING' : 'ENGINE STOPPED',
                                style: TextStyle(
                                  color: isRunning ? const Color(0xff22c55e) : (isDark ? const Color(0xff9fb4d0) : const Color(0xff64748b)),
                                  fontFamily: 'monospace',
                                  fontWeight: FontWeight.bold,
                                  fontSize: 13,
                                  letterSpacing: 0.8,
                                ),
                              ),
                              if (isRunning) ...[
                                const SizedBox(width: 8),
                                Container(
                                  width: 8,
                                  height: 8,
                                  decoration: const BoxDecoration(
                                    color: Color(0xff22c55e),
                                    shape: BoxShape.circle,
                                  ),
                                ),
                              ],
                            ],
                          ),
                          const SizedBox(height: 4),
                          Text(
                            isRunning
                                ? 'Live cycles updating dynamically'
                                : 'Tap Run MaxAlpha to start trading engine',
                            style: TextStyle(
                              color: isDark ? const Color(0xff9fb4d0) : const Color(0xff64748b),
                              fontSize: 11,
                            ),
                          ),
                        ],
                      ),
                    ),
                    IconButton(
                      icon: const Icon(Icons.refresh_rounded),
                      onPressed: refresh,
                      tooltip: 'Refresh Now',
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 12),

            // ── Market Regime Card ──────────────────────────────────────────
            Card(
              color: isDark ? const Color(0xff0b1729) : Colors.white,
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Text(
                          'MARKET REGIME',
                          style: TextStyle(
                            color: Color(0xff9fb4d0),
                            fontSize: 10,
                            letterSpacing: 1.1,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          regimeStr.toUpperCase(),
                          style: TextStyle(
                            color: isDark ? const Color(0xffedf5ff) : const Color(0xff10233d),
                            fontFamily: 'monospace',
                            fontSize: 18,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                      ],
                    ),
                    _regimeBadge(regimeStr),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 12),

            // ── Metric Grid ────────────────────────────────────────────────
            GridView.count(
              crossAxisCount: MediaQuery.of(context).size.width > 650 ? 4 : 2,
              childAspectRatio: 1.5,
              shrinkWrap: true,
              physics: const NeverScrollableScrollPhysics(),
              crossAxisSpacing: 10,
              mainAxisSpacing: 10,
              children: [
                _metric('PORTFOLIO', '₹${totalVal.toStringAsFixed(0)}', isDark),
                _metric('CASH', '₹${cashVal.toStringAsFixed(0)}', isDark),
                _metric('INVESTED', '₹${investedVal.toStringAsFixed(0)}', isDark),
                _metric('POSITIONS', '$posCount / 7', isDark),
              ],
            ),
            const SizedBox(height: 16),

            // ── Tier Allocation Section ────────────────────────────────────
            Text(
              'TIER ALLOCATION',
              style: TextStyle(
                color: isDark ? const Color(0xff9fb4d0) : const Color(0xff64748b),
                fontSize: 10,
                letterSpacing: 1.2,
                fontWeight: FontWeight.bold,
              ),
            ),
            const SizedBox(height: 8),
            _tierCard('Tier 1 — Momentum Smallcaps', 'Target: Rs 20 – Rs 250', t1, totalVal, const Color(0xfffb923c), isDark),
            const SizedBox(height: 8),
            _tierCard('Tier 2 — Midcaps', 'Target: Rs 250 – Rs 1,500', t2, totalVal, const Color(0xff3b82f6), isDark),
            const SizedBox(height: 8),
            _tierCard('Tier 3 — Blue Chip / ETF', 'Target: Core allocation', t3, totalVal, const Color(0xff22c55e), isDark),
            const SizedBox(height: 16),

            // ── Active Positions Section ───────────────────────────────────
            if (positionsList.isNotEmpty) ...[
              Text(
                'OPEN POSITIONS (${positionsList.length})',
                style: TextStyle(
                  color: isDark ? const Color(0xff9fb4d0) : const Color(0xff64748b),
                  fontSize: 10,
                  letterSpacing: 1.2,
                  fontWeight: FontWeight.bold,
                ),
              ),
              const SizedBox(height: 8),
              Card(
                color: isDark ? const Color(0xff10233d) : Colors.white,
                child: ListView.separated(
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  itemCount: positionsList.length,
                  separatorBuilder: (_, __) => Divider(height: 1, color: isDark ? const Color(0x284a74a9) : const Color(0xffe5e7eb)),
                  itemBuilder: (ctx, idx) {
                    final item = positionsList[idx] as Map<String, dynamic>;
                    final sym = item['symbol']?.toString() ?? 'STOCK';
                    final qty = item['qty'] ?? item['shares'] ?? 0;
                    final buyPx = num.tryParse(item['entry_price']?.toString() ?? item['price']?.toString() ?? '0') ?? 0;
                    final curPx = num.tryParse(item['current_price']?.toString() ?? buyPx.toString()) ?? buyPx;
                    final pnl = num.tryParse(item['pnl']?.toString() ?? '0') ?? (curPx - buyPx) * (num.tryParse(qty.toString()) ?? 1);
                    final isPos = pnl >= 0;

                    return ListTile(
                      dense: true,
                      title: Text(sym, style: const TextStyle(fontFamily: 'monospace', fontWeight: FontWeight.bold, fontSize: 14)),
                      subtitle: Text('Qty: $qty • Buy: ₹${buyPx.toStringAsFixed(1)}', style: TextStyle(color: isDark ? const Color(0xff9fb4d0) : const Color(0xff64748b), fontSize: 11)),
                      trailing: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        crossAxisAlignment: CrossAxisAlignment.end,
                        children: [
                          Text(
                            '₹${curPx.toStringAsFixed(1)}',
                            style: const TextStyle(fontFamily: 'monospace', fontWeight: FontWeight.w600, fontSize: 13),
                          ),
                          Text(
                            '${isPos ? '+' : ''}₹${pnl.toStringAsFixed(1)}',
                            style: TextStyle(color: isPos ? const Color(0xff22c55e) : const Color(0xffef4444), fontWeight: FontWeight.bold, fontSize: 11),
                          ),
                        ],
                      ),
                    );
                  },
                ),
              ),
              const SizedBox(height: 16),
            ],

            // ── Cycle History Log Section ──────────────────────────────────
            Text(
              'LIVE CYCLE HISTORY (${historyList.length})',
              style: TextStyle(
                color: isDark ? const Color(0xff9fb4d0) : const Color(0xff64748b),
                fontSize: 10,
                letterSpacing: 1.2,
                fontWeight: FontWeight.bold,
              ),
            ),
            const SizedBox(height: 8),
            Card(
              color: isDark ? const Color(0xff10233d) : Colors.white,
              child: historyList.isEmpty
                  ? Padding(
                      padding: const EdgeInsets.all(20),
                      child: Center(
                        child: Text(
                          'No cycles completed yet.\nEngine will record data after every trading cycle.',
                          textAlign: TextAlign.center,
                          style: TextStyle(color: isDark ? const Color(0xff9fb4d0) : const Color(0xff64748b), fontSize: 12),
                        ),
                      ),
                    )
                  : ListView.separated(
                      shrinkWrap: true,
                      physics: const NeverScrollableScrollPhysics(),
                      itemCount: historyList.length > 15 ? 15 : historyList.length,
                      separatorBuilder: (_, __) => Divider(height: 1, color: isDark ? const Color(0x284a74a9) : const Color(0xffe5e7eb)),
                      itemBuilder: (ctx, idx) {
                        // Reverse so latest is on top
                        final item = (historyList.reversed.toList())[idx] as Map<String, dynamic>;
                        final cycle = item['cycle'] ?? historyList.length - idx;
                        final tot = num.tryParse(item['total_usd']?.toString() ?? item['total']?.toString() ?? '0') ?? 0;
                        final reg = item['regime']?.toString() ?? 'NORMAL';
                        final sigs = item['signals'] ?? 0;
                        final ts = item['ts']?.toString() ?? '';
                        var timeDisplay = '';
                        if (ts.isNotEmpty) {
                          try {
                            final dt = DateTime.parse(ts).toLocal();
                            timeDisplay = '${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}';
                          } catch (_) {}
                        }

                        return ListTile(
                          dense: true,
                          leading: Container(
                            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                            decoration: BoxDecoration(
                              color: const Color(0xfff97316).withOpacity(0.15),
                              borderRadius: BorderRadius.circular(6),
                            ),
                            child: Text(
                              'C#$cycle',
                              style: const TextStyle(
                                fontFamily: 'monospace',
                                color: Color(0xfff97316),
                                fontWeight: FontWeight.bold,
                                fontSize: 11,
                              ),
                            ),
                          ),
                          title: Text(
                            'Portfolio: ₹${tot.toStringAsFixed(0)}',
                            style: const TextStyle(fontFamily: 'monospace', fontWeight: FontWeight.w600, fontSize: 13),
                          ),
                          subtitle: Text(
                            'Regime: $reg ${timeDisplay.isNotEmpty ? '• $timeDisplay' : ''}',
                            style: TextStyle(color: isDark ? const Color(0xff9fb4d0) : const Color(0xff64748b), fontSize: 11),
                          ),
                          trailing: Container(
                            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                            decoration: BoxDecoration(
                              color: sigs > 0 ? const Color(0x2022c55e) : (isDark ? const Color(0x2094a3b8) : const Color(0xfff1f5f9)),
                              borderRadius: BorderRadius.circular(6),
                            ),
                            child: Text(
                              '$sigs Signals',
                              style: TextStyle(
                                color: sigs > 0 ? const Color(0xff22c55e) : (isDark ? const Color(0xff9fb4d0) : const Color(0xff64748b)),
                                fontSize: 10,
                                fontWeight: FontWeight.bold,
                              ),
                            ),
                          ),
                        );
                      },
                    ),
            ),
            const SizedBox(height: 20),
          ],
        ),
      ),
    );
  }

  Widget _metric(String title, String value, bool isDark) => Card(
        color: isDark ? const Color(0xff0b1729) : Colors.white,
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Text(
                title,
                style: TextStyle(
                  color: isDark ? const Color(0xff9fb4d0) : const Color(0xff64748b),
                  fontSize: 10,
                  letterSpacing: 1,
                  fontWeight: FontWeight.bold,
                ),
              ),
              const SizedBox(height: 6),
              Text(
                value,
                style: TextStyle(
                  color: isDark ? const Color(0xffedf5ff) : const Color(0xff10233d),
                  fontFamily: 'monospace',
                  fontSize: 17,
                  fontWeight: FontWeight.bold,
                ),
              ),
            ],
          ),
        ),
      );

  Widget _tierCard(String title, String subtitle, num val, num total, Color color, bool isDark) {
    final pct = total > 0 ? (val / total).clamp(0.0, 1.0) : 0.0;
    return Card(
      color: isDark ? const Color(0xff10233d) : Colors.white,
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(title, style: TextStyle(color: isDark ? const Color(0xffedf5ff) : const Color(0xff10233d), fontWeight: FontWeight.w600, fontSize: 13)),
                Text('₹${val.toStringAsFixed(0)}', style: TextStyle(color: color, fontFamily: 'monospace', fontWeight: FontWeight.bold, fontSize: 13)),
              ],
            ),
            const SizedBox(height: 4),
            Text(subtitle, style: TextStyle(color: isDark ? const Color(0xff9fb4d0) : const Color(0xff64748b), fontSize: 11)),
            const SizedBox(height: 8),
            ClipRRect(
              borderRadius: BorderRadius.circular(4),
              child: LinearProgressIndicator(
                value: pct.toDouble(),
                backgroundColor: isDark ? const Color(0xff0b1729) : const Color(0xffe2e8f0),
                color: color,
                minHeight: 6,
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _regimeBadge(String regime) {
    final u = regime.toUpperCase();
    Color bg, fg;
    if (u.contains('BULL')) {
      bg = const Color(0x2022c55e);
      fg = const Color(0xff22c55e);
    } else if (u.contains('BEAR')) {
      bg = const Color(0x20ef4444);
      fg = const Color(0xffef4444);
    } else if (u.contains('CRASH')) {
      bg = const Color(0x30ef4444);
      fg = const Color(0xffef4444);
    } else {
      bg = const Color(0x203b82f6);
      fg = const Color(0xff3b82f6);
    }
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: fg.withOpacity(0.3)),
      ),
      child: Text(
        u,
        style: TextStyle(color: fg, fontFamily: 'monospace', fontWeight: FontWeight.bold, fontSize: 11),
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
      final snapshot = await widget.session.api.dashboard();
      final history = jsonEncode(snapshot['history'] ?? const []);
      await _controller.runJavaScript('''
        (function() {
          const dashboardHeader = document.querySelector('.hdr');
          if (dashboardHeader) dashboardHeader.style.display = 'none';
          // Flutter owns authentication. The bundled desktop dashboard has a
          // separate HTML password screen, which otherwise prevents boot() and
          // leaves the mobile view showing no data.
          const loginScreen = document.getElementById('ls');
          const app = document.getElementById('app');
          if (loginScreen) loginScreen.style.display = 'none';
          if (app) app.style.display = 'block';
          window.__maxAlphaHistory = $history;
          if (!window.__maxAlphaFlutterBooted) {
            window.__maxAlphaFlutterBooted = true;
            if (typeof boot === 'function') boot();
          } else if (typeof loadAll === 'function') {
            loadAll();
            if (typeof updateAgentStatus === 'function') updateAgentStatus();
          }
        })();
      ''');
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
      final snapshot = await widget.session.api.dashboard();
      await _controller.runJavaScript('window.__maxAlphaHistory = ${jsonEncode(snapshot['history'] ?? const [])}; if (document.getElementById("app").style.display === "block") { loadAll(); updateAgentStatus(); }');
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
      if (mounted)
        setState(() {
          lines = List<String>.from(d['lines'] ?? []);
          running = d['running'] == true;
        });
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
      if (context.mounted)
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(error.message)));
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
