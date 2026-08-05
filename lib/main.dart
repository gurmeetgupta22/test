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

// â”€â”€ THEME CONSTANTS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
bool _darkPalette = true;
Color get kBgBlack => _darkPalette ? Color(0xff0a0a0a) : Color(0xfffffdf7);
Color get kBgCard => _darkPalette ? Color(0xff141414) : Color(0xffffffff);
Color get kBgCard2 => _darkPalette ? Color(0xff1c1c1c) : Color(0xfffff8dc);
const kYellow = Color(0xffffd600);
const kYellowDim = Color(0xffffc107);
Color get kYellowGlow => _darkPalette ? Color(0x66ffd600) : Color(0x33b88600);
const kRedGlow = Color(0xffff1744);
const kBlueGlow = Color(0xff1565c0);
const kGreen = Color(0xff00e676);
const kRed = Color(0xffff1744);
Color get kTextPrimary => _darkPalette ? Color(0xfffafafa) : Color(0xff171717);
Color get kTextSub => _darkPalette ? Color(0xff9e9e9e) : Color(0xff4a4a4a);
Color get kBorder => _darkPalette ? Color(0xff2a2a2a) : Color(0xffd6c36a);

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  SystemChrome.setSystemUIOverlayStyle(
    SystemUiOverlayStyle(
      statusBarColor: Colors.transparent,
      statusBarIconBrightness: Brightness.light,
    ),
  );
  if (Platform.isIOS) {
    try {
      unawaited(
        SeriousPython.run(appFileName: 'main_ios.py').catchError((Object err) {
          debugPrint('SeriousPython iOS server startup error: $err');
          return null;
        }),
      );
    } catch (e) {
      debugPrint('SeriousPython startup exception: $e');
    }
  }
  runApp(MaxAlphaApp());
}

class ApiError implements Exception {
  ApiError(this.message);
  final String message;
}

const _iOSBotBaseUrl = 'http://127.0.0.1:8766';

Future<Map<String, dynamic>> _iosGet(String path) async {
  final uri = Uri.parse('$_iOSBotBaseUrl$path');
  late http.Response resp;
  for (var attempt = 0; attempt < 8; attempt++) {
    try {
      resp = await http.get(uri).timeout(Duration(seconds: 10));
      break;
    } catch (_) {
      if (attempt == 7) rethrow;
      await Future<void>.delayed(Duration(milliseconds: 500));
    }
  }
  final body = jsonDecode(resp.body) as Map<String, dynamic>;
  if (body['ok'] != true) {
    throw ApiError(body['error']?.toString() ?? 'iOS bot error on $path');
  }
  final result = body['result'];
  if (result == null) return {};
  if (result is Map<String, dynamic>) return result;
  return {'result': result};
}

Future<Map<String, dynamic>> _iosPost(String path, [Object? body]) async {
  final uri = Uri.parse('$_iOSBotBaseUrl$path');
  late http.Response resp;
  for (var attempt = 0; attempt < 8; attempt++) {
    try {
      resp = await http
          .post(
            uri,
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode(body ?? {}),
          )
          .timeout(Duration(seconds: 30));
      break;
    } catch (_) {
      if (attempt == 7) rethrow;
      await Future<void>.delayed(Duration(milliseconds: 500));
    }
  }
  final decoded = jsonDecode(resp.body) as Map<String, dynamic>;
  if (decoded['ok'] != true) {
    throw ApiError(decoded['error']?.toString() ?? 'iOS bot error on $path');
  }
  final result = decoded['result'];
  if (result == null) return {};
  if (result is Map<String, dynamic>) return result;
  return {'result': result};
}

class MobileApi {
  MobileApi(this.session);
  final Session session;
  static final _bridge = MethodChannel('com.maxalpha.mobile/bot');

  Future<T?> _call<T>(String method, [dynamic arguments]) async {
    if (Platform.isIOS) {
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
        case 'clearHistory':
          result = await _iosPost('/clearHistory');
          return result as T?;
        default:
          throw ApiError('Unknown bot method: $method');
      }
      return result as T?;
    }
    try {
      return await _bridge.invokeMethod<T>(method, arguments);
    } on PlatformException catch (error) {
      throw ApiError(error.message ?? 'Python engine error: $method');
    } on MissingPluginException {
      throw ApiError('Local engine only available in installed app.');
    }
  }

  Future<Map<String, dynamic>> config() async => {
    'configured': session.configured,
  };
  Future<void> saveConfig(Map<String, String> value) async {
    await _call<dynamic>('configure', value);
    await session.saveBotConfig(value);
  }

  Future<Map<String, dynamic>> dashboard() async =>
      Map<String, dynamic>.from(await _call<Map>('dashboard') ?? {});
  Future<Map<String, dynamic>> logs() async =>
      Map<String, dynamic>.from(await _call<Map>('logs') ?? {});
  Future<Map<String, dynamic>> signals() async =>
      Map<String, dynamic>.from(await _call<Map>('signals') ?? {});
  Future<Map<String, dynamic>> startDashboard() async =>
      Map<String, dynamic>.from(await _call<Map>('startDashboard') ?? {});
  Future<void> start(double? amount) async {
    await _call<dynamic>('startBot', {
      'budget': amount,
      'configuration': session.botConfig,
    });
  }

  Future<void> stop() => _call<dynamic>('stopBot');
  Future<Map<String, dynamic>> clearHistory() async =>
      Map<String, dynamic>.from(await _call<Map>('clearHistory') ?? {});
  Future<void> logout() async {}
}

class Session extends ChangeNotifier {
  static final _secure = FlutterSecureStorage();
  late SharedPreferences _prefs;
  String deviceId = '';
  String? _activeSessionToken;
  bool get isUnlocked => _activeSessionToken != null;
  late MobileApi api;
  bool dark = true;
  bool ready = false;
  bool configured = false;
  String? dashboardUrl;
  Map<String, String> botConfig = {};
  Map<String, dynamic> dashboardData = {};
  List<String> logLines = [];
  bool isEngineRunning = false;
  bool isPolling = false;
  Timer? _poller;

  bool _mapEquals(Map<String, dynamic> a, Map<String, dynamic> b) {
    if (a.length != b.length) return false;
    for (final entry in a.entries) {
      if (b[entry.key] != entry.value) return false;
    }
    return true;
  }

  bool _listEquals(List<String> a, List<String> b) {
    if (a.length != b.length) return false;
    for (var i = 0; i < a.length; i++) {
      if (a[i] != b[i]) return false;
    }
    return true;
  }

  Future<void> load() async {
    _prefs = await SharedPreferences.getInstance();
    deviceId = _prefs.getString('device_id') ?? _id();
    _activeSessionToken = null;
    dark = _prefs.getBool('dark_theme') ?? true;
    _darkPalette = dark;
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
    startPolling();
  }

  void startPolling() {
    _poller?.cancel();
    _poller = Timer.periodic(Duration(seconds: 8), (_) => fetchLatestData());
    fetchLatestData();
  }

  void stopPolling() {
    _poller?.cancel();
    _poller = null;
  }

  Future<void> fetchLatestData() async {
    if (isPolling) return;
    isPolling = true;
    try {
      final dash = await api.dashboard();
      final l = await api.logs();
      final nextRunning = l['running'] == true;
      final nextLines = List<String>.from(l['lines'] ?? []);
      final shouldNotify =
          nextRunning != isEngineRunning ||
          !_mapEquals(dash, dashboardData) ||
          !_listEquals(nextLines, logLines);
      isEngineRunning = nextRunning;
      dash['running'] = isEngineRunning;
      dashboardData = dash;
      logLines = nextLines;
      if (shouldNotify) notifyListeners();
    } catch (_) {
    } finally {
      isPolling = false;
    }
  }

  String _id() =>
      List.generate(
        32,
        (_) => 'abcdef0123456789'[Random.secure().nextInt(16)],
      ).join();
  String _newSessionToken() =>
      List.generate(
        64,
        (_) => 'abcdef0123456789'[Random.secure().nextInt(16)],
      ).join();

  Future<String> createAccount(String email) async {
    final clean = email.trim().toLowerCase();
    if (!clean.contains('@')) throw ApiError('Enter a valid email address.');
    final stem = clean.split('@').first.replaceAll(RegExp(r'[^a-z0-9]'), '');
    if (stem.isEmpty) {
      throw ApiError('Enter email with letters or numbers before @.');
    }
    final password =
        '${stem.substring(0, min(5, stem.length))}@${100000 + Random.secure().nextInt(900000)}';
    await _prefs.setString('account_email', clean);
    await _secure.write(key: 'account_password', value: password);
    return password;
  }

  Future<void> login(String email, String password) async {
    if (email.trim().toLowerCase() != _prefs.getString('account_email') ||
        password != await _secure.read(key: 'account_password')) {
      throw ApiError('Email or password is incorrect for this device.');
    }
    _activeSessionToken = _newSessionToken();
    notifyListeners();
    startPolling();
  }

  Future<void> saveBotConfig(Map<String, String> value) async {
    botConfig = {...botConfig, ...value};
    configured = true;
    for (final entry in botConfig.entries) {
      if ({
        'dhan_client_id',
        'dhan_access_token',
        'ai_key',
      }.contains(entry.key)) {
        await _secure.write(key: entry.key, value: entry.value);
      } else {
        await _prefs.setString(entry.key, entry.value);
      }
    }
    await _prefs.setBool('bot_configured', true);
  }

  Future<void> signOut() async {
    stopPolling();
    _activeSessionToken = null;
    notifyListeners();
  }

  void setDashboardUrl(String? value) {
    dashboardUrl = value;
    notifyListeners();
  }

  Future<void> toggleTheme(bool value) async {
    dark = value;
    _darkPalette = value;
    notifyListeners();
    SystemChrome.setSystemUIOverlayStyle(
      SystemUiOverlayStyle(
        statusBarColor: Colors.transparent,
        statusBarIconBrightness: value ? Brightness.light : Brightness.dark,
        systemNavigationBarColor: value ? Color(0xff0a0a0a) : Color(0xfffffdf7),
        systemNavigationBarIconBrightness:
            value ? Brightness.light : Brightness.dark,
      ),
    );
    await _prefs.setBool('dark_theme', value);
  }
}

// â”€â”€ SHARED WIDGETS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Widget glowBox({
  required Widget child,
  Color? glowColor,
  Color? bg,
  double radius = 16,
  EdgeInsets? padding,
}) {
  final effectiveGlowColor = glowColor ?? kBlueGlow;
  return Container(
    decoration: BoxDecoration(
      color: bg ?? kBgCard,
      borderRadius: BorderRadius.circular(radius),
      border: Border.all(
        color: effectiveGlowColor.withValues(alpha: 0.45),
        width: 1.2,
      ),
      boxShadow: [
        BoxShadow(
          color: effectiveGlowColor.withValues(alpha: 0.15),
          blurRadius: 12,
          spreadRadius: 1,
        ),
      ],
    ),
    padding: padding ?? EdgeInsets.all(14),
    child: child,
  );
}

Widget sectionHeading(String title) {
  return Padding(
    padding: EdgeInsets.only(bottom: 12),
    child: Text(
      title,
      style: TextStyle(
        color: kTextPrimary,
        fontSize: 18,
        fontWeight: FontWeight.w800,
        letterSpacing: 1.4,
        shadows: [
          Shadow(color: kRedGlow, blurRadius: 14),
          Shadow(color: kRedGlow, blurRadius: 5),
        ],
      ),
    ),
  );
}

Widget yellowButton({
  required String label,
  required VoidCallback? onPressed,
  IconData? icon,
  bool isDestructive = false,
}) {
  final bg = isDestructive ? kRed : kYellow;
  final fg = isDestructive ? Colors.white : Colors.black;
  return AnimatedOpacity(
    duration: Duration(milliseconds: 200),
    opacity: onPressed == null ? 0.4 : 1.0,
    child: GestureDetector(
      onTap: onPressed,
      child: Container(
        height: 52,
        decoration: BoxDecoration(
          gradient: LinearGradient(
            colors:
                isDestructive
                    ? [kRed, Color(0xffd32f2f)]
                    : [kYellow, kYellowDim],
          ),
          borderRadius: BorderRadius.circular(14),
          boxShadow: [
            BoxShadow(
              color: bg.withValues(alpha: 0.4),
              blurRadius: 12,
              spreadRadius: 1,
              offset: Offset(0, 3),
            ),
          ],
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            if (icon != null) ...[
              Icon(icon, color: fg, size: 20),
              SizedBox(width: 8),
            ],
            Text(
              label,
              style: TextStyle(
                color: fg,
                fontWeight: FontWeight.w800,
                fontSize: 14,
                letterSpacing: 0.5,
              ),
            ),
          ],
        ),
      ),
    ),
  );
}

Widget outlineButton({
  required String label,
  required VoidCallback? onPressed,
  IconData? icon,
}) {
  return AnimatedOpacity(
    duration: Duration(milliseconds: 200),
    opacity: onPressed == null ? 0.35 : 1.0,
    child: GestureDetector(
      onTap: onPressed,
      child: Container(
        height: 48,
        decoration: BoxDecoration(
          color: kBgCard2,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: kBorder, width: 1.5),
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            if (icon != null) ...[
              Icon(icon, color: kTextSub, size: 18),
              SizedBox(width: 7),
            ],
            Text(
              label,
              style: TextStyle(
                color: kTextSub,
                fontWeight: FontWeight.w700,
                fontSize: 13,
              ),
            ),
          ],
        ),
      ),
    ),
  );
}

// â”€â”€ UNIFIED START BOT SHEET â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
/// Budget field starts EMPTY. Leaving it blank = use last session wallet cap.
Future<void> showStartBotSheet(BuildContext context, Session session) async {
  final budgetController = TextEditingController();
  String selectedMode = session.botConfig['trading_mode'] ?? 'paper';

  await showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    backgroundColor: Colors.transparent,
    builder:
        (ctx) => StatefulBuilder(
          builder:
              (ctx, setS) => Container(
                padding: EdgeInsets.fromLTRB(
                  20,
                  20,
                  20,
                  MediaQuery.of(ctx).viewInsets.bottom + 28,
                ),
                decoration: BoxDecoration(
                  color: kBgCard,
                  borderRadius: BorderRadius.vertical(top: Radius.circular(28)),
                  border: Border.all(
                    color: kBlueGlow.withValues(alpha: 0.35),
                    width: 1.2,
                  ),
                  boxShadow: [
                    BoxShadow(
                      color: kBlueGlow.withValues(alpha: 0.2),
                      blurRadius: 20,
                      spreadRadius: 2,
                    ),
                  ],
                ),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Center(
                      child: Container(
                        width: 40,
                        height: 4,
                        decoration: BoxDecoration(
                          color: kBorder,
                          borderRadius: BorderRadius.circular(2),
                        ),
                      ),
                    ),
                    SizedBox(height: 18),
                    Row(
                      children: [
                        Container(
                          padding: EdgeInsets.all(10),
                          decoration: BoxDecoration(
                            color: kYellow.withValues(alpha: 0.15),
                            borderRadius: BorderRadius.circular(12),
                            border: Border.all(
                              color: kYellow.withValues(alpha: 0.4),
                            ),
                          ),
                          child: Icon(
                            Icons.play_arrow_rounded,
                            color: kYellow,
                            size: 24,
                          ),
                        ),
                        SizedBox(width: 12),
                        Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              'START MAX ALPHA QUANT',
                              style: TextStyle(
                                fontFamily: 'monospace',
                                fontWeight: FontWeight.bold,
                                fontSize: 15,
                                color: kTextPrimary,
                                letterSpacing: 0.6,
                              ),
                            ),
                            SizedBox(height: 3),
                            Text(
                              'Configure budget & execution mode',
                              style: TextStyle(color: kTextSub, fontSize: 11),
                            ),
                          ],
                        ),
                      ],
                    ),
                    SizedBox(height: 22),
                    Text(
                      'TRADING BUDGET (Rs)',
                      style: TextStyle(
                        color: kTextSub,
                        fontSize: 10,
                        letterSpacing: 1.2,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    SizedBox(height: 8),
                    Container(
                      decoration: BoxDecoration(
                        color: kBgCard2,
                        borderRadius: BorderRadius.circular(12),
                        border: Border.all(
                          color: kYellow.withValues(alpha: 0.4),
                          width: 1.2,
                        ),
                      ),
                      child: TextField(
                        controller: budgetController,
                        keyboardType: TextInputType.number,
                        style: TextStyle(
                          color: kTextPrimary,
                          fontFamily: 'monospace',
                          fontWeight: FontWeight.bold,
                          fontSize: 16,
                        ),
                        decoration: InputDecoration(
                          prefixText: 'Rs ',
                          prefixStyle: TextStyle(
                            color: kYellow,
                            fontFamily: 'monospace',
                            fontWeight: FontWeight.bold,
                          ),
                          hintText:
                              selectedMode == 'live'
                                  ? 'Blank = keep allocation; amount = add to it'
                                  : 'Leave blank to use previous wallet cap',
                          hintStyle: TextStyle(color: kTextSub, fontSize: 12),
                          border: InputBorder.none,
                          contentPadding: EdgeInsets.symmetric(
                            horizontal: 16,
                            vertical: 15,
                          ),
                        ),
                        onChanged: (_) => setS(() {}),
                      ),
                    ),
                    SizedBox(height: 10),
                    SingleChildScrollView(
                      scrollDirection: Axis.horizontal,
                      child: Row(
                        children:
                            ['10000', '25000', '50000', '100000'].map((preset) {
                              final sel = budgetController.text == preset;
                              return Padding(
                                padding: EdgeInsets.only(right: 8),
                                child: GestureDetector(
                                  onTap:
                                      () => setS(
                                        () => budgetController.text = preset,
                                      ),
                                  child: AnimatedContainer(
                                    duration: Duration(milliseconds: 180),
                                    padding: EdgeInsets.symmetric(
                                      horizontal: 14,
                                      vertical: 8,
                                    ),
                                    decoration: BoxDecoration(
                                      color: sel ? kYellow : kBgCard2,
                                      borderRadius: BorderRadius.circular(20),
                                      border: Border.all(
                                        color: sel ? kYellow : kBorder,
                                      ),
                                      boxShadow:
                                          sel
                                              ? [
                                                BoxShadow(
                                                  color: kYellow.withValues(
                                                    alpha: 0.35,
                                                  ),
                                                  blurRadius: 8,
                                                ),
                                              ]
                                              : null,
                                    ),
                                    child: Text(
                                      int.parse(preset) >= 100000
                                          ? 'Rs 1L'
                                          : 'Rs ${int.parse(preset) ~/ 1000}k',
                                      style: TextStyle(
                                        color: sel ? Colors.black : kTextSub,
                                        fontWeight: FontWeight.w700,
                                        fontSize: 12,
                                        fontFamily: 'monospace',
                                      ),
                                    ),
                                  ),
                                ),
                              );
                            }).toList(),
                      ),
                    ),
                    SizedBox(height: 20),
                    Text(
                      'EXECUTION MODE',
                      style: TextStyle(
                        color: kTextSub,
                        fontSize: 10,
                        letterSpacing: 1.2,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    SizedBox(height: 10),
                    Container(
                      height: 46,
                      decoration: BoxDecoration(
                        color: kBgCard2,
                        borderRadius: BorderRadius.circular(12),
                        border: Border.all(color: kBorder),
                      ),
                      child: Row(
                        children:
                            ['paper', 'live'].map((mode) {
                              final sel = selectedMode == mode;
                              return Expanded(
                                child: GestureDetector(
                                  onTap: () => setS(() => selectedMode = mode),
                                  child: AnimatedContainer(
                                    duration: Duration(milliseconds: 200),
                                    margin: EdgeInsets.all(4),
                                    decoration: BoxDecoration(
                                      color: sel ? kYellow : Colors.transparent,
                                      borderRadius: BorderRadius.circular(9),
                                      boxShadow:
                                          sel
                                              ? [
                                                BoxShadow(
                                                  color: kYellow.withValues(
                                                    alpha: 0.4,
                                                  ),
                                                  blurRadius: 8,
                                                ),
                                              ]
                                              : null,
                                    ),
                                    child: Center(
                                      child: Text(
                                        mode == 'paper'
                                            ? 'Paper Trading'
                                            : 'Live Dhan Broker',
                                        style: TextStyle(
                                          color: sel ? Colors.black : kTextSub,
                                          fontWeight: FontWeight.w700,
                                          fontSize: 12,
                                        ),
                                      ),
                                    ),
                                  ),
                                ),
                              );
                            }).toList(),
                      ),
                    ),
                    SizedBox(height: 26),
                    Row(
                      children: [
                        Expanded(
                          child: outlineButton(
                            label: 'Cancel',
                            onPressed: () => Navigator.pop(ctx),
                          ),
                        ),
                        SizedBox(width: 12),
                        Expanded(
                          flex: 2,
                          child: yellowButton(
                            label: 'LAUNCH ENGINE',
                            icon: Icons.flash_on_rounded,
                            onPressed: () async {
                              Navigator.pop(ctx);
                              final raw = budgetController.text.trim();
                              final val =
                                  raw.isEmpty
                                      ? null
                                      : double.tryParse(
                                        raw.replaceAll(',', ''),
                                      );
                              await session.saveBotConfig({
                                'trading_mode': selectedMode,
                              });
                              await session.api.start(val);
                              await session.fetchLatestData();
                            },
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
        ),
  );
}

// â”€â”€ THEME â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class MaxAlphaApp extends StatefulWidget {
  const MaxAlphaApp({super.key});
  @override
  State<MaxAlphaApp> createState() => _MaxAlphaAppState();
}

class _MaxAlphaAppState extends State<MaxAlphaApp> {
  final session = Session();
  bool _ready = false;
  bool _unlocked = false;
  bool _configured = false;
  bool _dark = true;

  @override
  void initState() {
    super.initState();
    session.addListener(_syncFromSession);
    session.load();
  }

  void _syncFromSession() {
    final nextReady = session.ready;
    final nextUnlocked = session.isUnlocked;
    final nextConfigured = session.configured;
    final nextDark = session.dark;
    if (nextReady == _ready &&
        nextUnlocked == _unlocked &&
        nextConfigured == _configured &&
        nextDark == _dark) {
      return;
    }
    setState(() {
      _ready = nextReady;
      _unlocked = nextUnlocked;
      _configured = nextConfigured;
      _dark = nextDark;
    });
  }

  @override
  void dispose() {
    session.removeListener(_syncFromSession);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'MaxAlpha',
      theme: _buildTheme(false),
      darkTheme: _buildTheme(true),
      themeMode: _dark ? ThemeMode.dark : ThemeMode.light,
      home:
          !_ready
              ? Scaffold(
                backgroundColor: kBgBlack,
                body: Center(child: CircularProgressIndicator(color: kYellow)),
              )
              : !_unlocked
              ? AuthPage(session: session)
              : AppShell(session: session),
    );
  }

  ThemeData _buildTheme(bool dark) {
    return ThemeData(
      useMaterial3: true,
      brightness: dark ? Brightness.dark : Brightness.light,
      scaffoldBackgroundColor: dark ? Color(0xff0a0a0a) : Color(0xfffffdf7),
      colorScheme: ColorScheme.fromSeed(
        seedColor: kYellow,
        brightness: dark ? Brightness.dark : Brightness.light,
      ).copyWith(
        primary: kYellow,
        onPrimary: Colors.black,
        secondary: kYellow,
        surface: dark ? Color(0xff141414) : Colors.white,
        onSurface: dark ? Color(0xfffafafa) : Color(0xff171717),
      ),
      appBarTheme: AppBarTheme(
        elevation: 0,
        backgroundColor: dark ? Color(0xff0a0a0a) : Color(0xfffffdf7),
        foregroundColor: dark ? Color(0xfffafafa) : Color(0xff171717),
        titleTextStyle: TextStyle(
          fontFamily: 'monospace',
          fontWeight: FontWeight.bold,
          fontSize: 18,
          letterSpacing: 1,
          color: dark ? Color(0xfffafafa) : Color(0xff171717),
        ),
      ),
      cardTheme: CardThemeData(
        elevation: 0,
        color: dark ? Color(0xff141414) : Colors.white,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: BorderSide(color: dark ? Color(0xff2a2a2a) : Color(0xffd6c36a)),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: kBgCard2,
        contentPadding: EdgeInsets.symmetric(horizontal: 16, vertical: 17),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide.none,
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: kBorder),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: kYellow, width: 1.5),
        ),
        hintStyle: TextStyle(color: kTextSub),
        labelStyle: TextStyle(color: kTextSub),
      ),
      snackBarTheme: SnackBarThemeData(
        backgroundColor: kBgCard,
        contentTextStyle: TextStyle(color: kTextPrimary),
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      ),
    );
  }
}

// â”€â”€ AUTH PAGE â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class AuthPage extends StatefulWidget {
  const AuthPage({super.key, required this.session});
  final Session session;
  @override
  State<AuthPage> createState() => _AuthPageState();
}

class _AuthPageState extends State<AuthPage> {
  final email = TextEditingController(), password = TextEditingController();
  bool register = false, waiting = false, showPass = false;
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
          builder:
              (ctx) => AlertDialog(
                backgroundColor: kBgCard,
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(18),
                  side: BorderSide(color: kYellow, width: 1.2),
                ),
                title: Text(
                  'Save this password',
                  style: TextStyle(color: kYellow, fontWeight: FontWeight.bold),
                ),
                content: SelectableText(
                  'Your Max Alpha password:\n\n$generated\n\nStored only on this device.',
                  style: TextStyle(
                    fontFamily: 'monospace',
                    fontSize: 12,
                    color: kTextPrimary,
                  ),
                ),
                actions: [
                  GestureDetector(
                    onTap: () => Navigator.pop(ctx),
                    child: Container(
                      padding: EdgeInsets.symmetric(
                        horizontal: 24,
                        vertical: 10,
                      ),
                      decoration: BoxDecoration(
                        color: kYellow,
                        borderRadius: BorderRadius.circular(10),
                      ),
                      child: Text(
                        'I saved it',
                        style: TextStyle(
                          color: Colors.black,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ),
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
    backgroundColor: kBgBlack,
    body: Container(
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [kBgBlack, Color(0xff0d0d0d), kBgBlack],
        ),
      ),
      child: Center(
        child: SingleChildScrollView(
          padding: EdgeInsets.all(24),
          child: ConstrainedBox(
            constraints: BoxConstraints(maxWidth: 430),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Container(
                  width: 160,
                  height: 160,
                  decoration: BoxDecoration(
                    color: kYellow.withValues(alpha: 0.12),
                    borderRadius: BorderRadius.circular(40),
                    border: Border.all(
                      color: kYellow.withValues(alpha: 0.5),
                      width: 1.5,
                    ),
                    boxShadow: [
                      BoxShadow(
                        color: kYellow.withValues(alpha: 0.3),
                        blurRadius: 24,
                        spreadRadius: 2,
                      ),
                    ],
                  ),
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(38),
                    child: Image.asset(
                      'assets/app2.png',
                      width: 160,
                      height: 160,
                      fit: BoxFit.cover,
                    ),
                  ),
                ),
                SizedBox(height: 20),
                Text(
                  'MAX ALPHA',
                  style: TextStyle(
                    color: kYellow,
                    fontFamily: 'monospace',
                    fontWeight: FontWeight.w900,
                    fontSize: 28,
                    letterSpacing: 3,
                    shadows: [Shadow(color: kYellowGlow, blurRadius: 18)],
                  ),
                ),
                SizedBox(height: 6),
                Text(
                  register
                      ? 'Create your device-bound account'
                      : 'Sign in to your trading console',
                  style: TextStyle(color: kTextSub, fontSize: 13),
                ),
                SizedBox(height: 32),
                glowBox(
                  glowColor: kBlueGlow,
                  radius: 20,
                  padding: EdgeInsets.all(24),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      TextField(
                        controller: email,
                        keyboardType: TextInputType.emailAddress,
                        style: TextStyle(color: kTextPrimary),
                        decoration: InputDecoration(
                          labelText: 'Email address',
                          prefixIcon: Icon(
                            Icons.email_outlined,
                            color: kTextSub,
                            size: 20,
                          ),
                        ),
                      ),
                      if (!register) ...[
                        SizedBox(height: 14),
                        TextField(
                          controller: password,
                          obscureText: !showPass,
                          style: TextStyle(color: kTextPrimary),
                          decoration: InputDecoration(
                            labelText: 'Password',
                            prefixIcon: Icon(
                              Icons.lock_outline,
                              color: kTextSub,
                              size: 20,
                            ),
                            suffixIcon: IconButton(
                              icon: Icon(
                                showPass
                                    ? Icons.visibility_off_outlined
                                    : Icons.visibility_outlined,
                                color: kTextSub,
                                size: 20,
                              ),
                              onPressed:
                                  () => setState(() => showPass = !showPass),
                            ),
                          ),
                        ),
                      ],
                      if (error != null)
                        Padding(
                          padding: EdgeInsets.only(top: 12),
                          child: Container(
                            padding: EdgeInsets.all(10),
                            decoration: BoxDecoration(
                              color: kRed.withValues(alpha: 0.15),
                              borderRadius: BorderRadius.circular(8),
                              border: Border.all(
                                color: kRed.withValues(alpha: 0.5),
                              ),
                            ),
                            child: Text(
                              error!,
                              style: TextStyle(color: kRed, fontSize: 12),
                            ),
                          ),
                        ),
                      SizedBox(height: 22),
                      yellowButton(
                        label:
                            waiting
                                ? 'Please wait...'
                                : (register ? 'GENERATE PASSWORD' : 'SIGN IN'),
                        icon:
                            register
                                ? Icons.key_rounded
                                : Icons.arrow_forward_rounded,
                        onPressed: waiting ? null : submit,
                      ),
                      SizedBox(height: 12),
                      GestureDetector(
                        onTap:
                            waiting
                                ? null
                                : () => setState(() {
                                  register = !register;
                                  error = null;
                                }),
                        child: Center(
                          child: Text(
                            register
                                ? 'Already have an account? Sign in'
                                : 'First time? Create an account',
                            style: TextStyle(
                              color: kYellow,
                              fontSize: 12,
                              decoration: TextDecoration.underline,
                            ),
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    ),
  );
}

// â”€â”€ APP SHELL â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class AppShell extends StatefulWidget {
  const AppShell({super.key, required this.session});
  final Session session;
  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  int page = 0;
  bool setupChecked = false, setupNeeded = false;

  @override
  void initState() {
    super.initState();
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
      return Scaffold(
        backgroundColor: kBgBlack,
        body: Center(child: CircularProgressIndicator(color: kYellow)),
      );
    }
    final tabLabels = ['Dashboard', 'Run Engine', 'Settings'];
    final title = setupNeeded ? 'Bot Setup' : tabLabels[page];
    final isRunning = widget.session.isEngineRunning;
    return Scaffold(
      backgroundColor: kBgBlack,
      appBar: AppBar(
        backgroundColor: kBgBlack,
        toolbarHeight: 64,
        titleSpacing: 8,
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Row(
              children: [
                Text(
                  title,
                  style: TextStyle(
                    fontFamily: 'monospace',
                    fontWeight: FontWeight.w800,
                    fontSize: 17,
                    letterSpacing: .6,
                    color: kYellow,
                    shadows: [
                      Shadow(color: kYellowGlow, blurRadius: 12),
                      Shadow(color: kYellowGlow, blurRadius: 5),
                    ],
                  ),
                ),
                SizedBox(width: 8),
                Container(
                  width: 9,
                  height: 9,
                  decoration: BoxDecoration(
                    color: isRunning ? kGreen : kTextSub,
                    shape: BoxShape.circle,
                    boxShadow:
                        isRunning
                            ? [
                              BoxShadow(
                                color: kGreen,
                                blurRadius: 8,
                                spreadRadius: 1,
                              ),
                            ]
                            : null,
                  ),
                ),
              ],
            ),
            SizedBox(height: 2),
            AnimatedBuilder(
              animation: widget.session,
              builder:
                  (_, _) => Text(
                    isRunning
                        ? 'ENGINE ACTIVE â€¢ DISCOVER MODE'
                        : 'LOCAL ENGINE STANDBY',
                    style: TextStyle(
                      color: kTextSub,
                      fontSize: 9,
                      letterSpacing: 1.2,
                    ),
                  ),
            ),
          ],
        ),
        actions: [
          IconButton(
            icon: Icon(Icons.refresh_rounded, color: kYellow),
            onPressed: () => widget.session.fetchLatestData(),
          ),
          SizedBox(width: 4),
        ],
        leading: Builder(
          builder:
              (ctx) => IconButton(
                icon: Icon(Icons.menu_rounded, color: kTextPrimary),
                onPressed: () => Scaffold.of(ctx).openDrawer(),
              ),
        ),
        bottom: PreferredSize(
          preferredSize: Size.fromHeight(1),
          child: Container(height: 1, color: kBorder),
        ),
      ),
      drawer: _buildDrawer(isRunning),
      body: SafeArea(
        top: false,
        child:
            setupNeeded
                ? SetupPage(
                  session: widget.session,
                  onComplete: () => setState(() => setupNeeded = false),
                )
                : IndexedStack(
                  index: page,
                  children: [
                    ExactDashboardPage(session: widget.session),
                    RunPage(session: widget.session),
                    SettingsPage(
                      session: widget.session,
                      onLogout: () => widget.session.signOut(),
                    ),
                  ],
                ),
      ),
    );
  }

  Widget _buildDrawer(bool isRunning) {
    return Drawer(
      backgroundColor: kBgBlack,
      child: Column(
        children: [
          Container(
            width: double.infinity,
            padding: EdgeInsets.fromLTRB(24, 56, 24, 24),
            decoration: BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: [Color(0xff111111), kBgCard],
              ),
              border: Border(
                bottom: BorderSide(color: kYellow.withValues(alpha: 0.3)),
              ),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  width: 100,
                  height: 100,
                  decoration: BoxDecoration(
                    color: kYellow.withValues(alpha: 0.12),
                    borderRadius: BorderRadius.circular(20),
                    border: Border.all(color: kYellow.withValues(alpha: 0.4)),
                  ),
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(18),
                    child: Image.asset(
                      'assets/app2.png',
                      width: 100,
                      height: 100,
                      fit: BoxFit.cover,
                    ),
                  ),
                ),
                SizedBox(height: 14),
                Text(
                  'MAX ALPHA',
                  style: TextStyle(
                    color: kYellow,
                    fontFamily: 'monospace',
                    fontWeight: FontWeight.w900,
                    fontSize: 22,
                    letterSpacing: 2,
                    shadows: [Shadow(color: kYellowGlow, blurRadius: 12)],
                  ),
                ),
                SizedBox(height: 6),
                Row(
                  children: [
                    Container(
                      width: 7,
                      height: 7,
                      decoration: BoxDecoration(
                        color: isRunning ? kGreen : kTextSub,
                        shape: BoxShape.circle,
                        boxShadow:
                            isRunning
                                ? [BoxShadow(color: kGreen, blurRadius: 6)]
                                : null,
                      ),
                    ),
                    SizedBox(width: 6),
                    Text(
                      isRunning ? 'ENGINE RUNNING' : 'ENGINE STANDBY',
                      style: TextStyle(
                        color: isRunning ? kGreen : kTextSub,
                        fontSize: 10,
                        fontWeight: FontWeight.bold,
                        letterSpacing: 1.1,
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
          SizedBox(height: 12),
          Padding(
            padding: EdgeInsets.symmetric(horizontal: 20, vertical: 4),
            child: Text(
              'NAVIGATION',
              style: TextStyle(
                color: kTextSub.withValues(alpha: 0.7),
                fontSize: 10,
                fontWeight: FontWeight.bold,
                letterSpacing: 1.3,
              ),
            ),
          ),
          SizedBox(height: 4),
          _navItem(0, Icons.grid_view_rounded, 'Dashboard'),
          _navItem(1, Icons.terminal_rounded, 'Run Engine & Logs'),
          Padding(
            padding: EdgeInsets.symmetric(horizontal: 20, vertical: 8),
            child: Divider(color: kBorder),
          ),
          _navItem(2, Icons.tune_rounded, 'Settings & Control'),
        ],
      ),
    );
  }

  Widget _navItem(int index, IconData icon, String label) {
    final selected = page == index;
    return Padding(
      padding: EdgeInsets.symmetric(horizontal: 14, vertical: 3),
      child: GestureDetector(
        onTap: () {
          Navigator.pop(context);
          setState(() => page = index);
        },
        child: AnimatedContainer(
          duration: Duration(milliseconds: 200),
          padding: EdgeInsets.symmetric(horizontal: 14, vertical: 13),
          decoration: BoxDecoration(
            color:
                selected ? kYellow.withValues(alpha: 0.14) : Colors.transparent,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(
              color:
                  selected
                      ? kYellow.withValues(alpha: 0.35)
                      : Colors.transparent,
              width: 1,
            ),
          ),
          child: Row(
            children: [
              Icon(icon, color: selected ? kYellow : kTextSub, size: 20),
              SizedBox(width: 12),
              // Use white text so label is always readable on the yellow-tinted bg
              Expanded(
                child: Text(
                  label,
                  style: TextStyle(
                    color: selected ? kTextPrimary : kTextSub,
                    fontWeight: selected ? FontWeight.w800 : FontWeight.w600,
                    fontSize: 13.5,
                  ),
                ),
              ),
              if (selected)
                Icon(Icons.chevron_right_rounded, color: kYellow, size: 18),
            ],
          ),
        ),
      ),
    );
  }
}

// â”€â”€ SETUP PAGE â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
  bool showDhanToken = false, showAiKey = false;

  @override
  void initState() {
    super.initState();
    final c = widget.session.botConfig;
    mode = c['trading_mode'] == 'live' ? 'live' : 'paper';
    dhanId.text = c['dhan_client_id'] ?? '';
    dhanToken.text = c['dhan_access_token'] ?? '';
    aiKey.text = c['ai_key'] ?? '';
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
      if (mode == 'live' &&
          (dhanId.text.trim().isEmpty || dhanToken.text.trim().isEmpty)) {
        throw ApiError(
          'Dhan client ID and access token are required for live trading.',
        );
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
    padding: EdgeInsets.all(20),
    children: [
      sectionHeading('BOT SETUP'),
      Text(
        'Credentials stay on this device and are supplied to the local Python engine only when MaxAlpha runs.',
        style: TextStyle(color: kTextSub, fontSize: 12, height: 1.5),
      ),
      SizedBox(height: 20),
      glowBox(
        glowColor: kYellow.withValues(alpha: 0.4),
        padding: EdgeInsets.all(6),
        child: Row(
          children:
              ['paper', 'live'].map((m) {
                final sel = mode == m;
                return Expanded(
                  child: GestureDetector(
                    onTap: () => setState(() => mode = m),
                    child: AnimatedContainer(
                      duration: Duration(milliseconds: 200),
                      padding: EdgeInsets.symmetric(vertical: 12),
                      decoration: BoxDecoration(
                        color: sel ? kYellow : Colors.transparent,
                        borderRadius: BorderRadius.circular(10),
                        boxShadow:
                            sel
                                ? [
                                  BoxShadow(
                                    color: kYellow.withValues(alpha: 0.35),
                                    blurRadius: 8,
                                  ),
                                ]
                                : null,
                      ),
                      child: Center(
                        child: Text(
                          m == 'paper' ? 'Paper Trading' : 'Live Trading',
                          style: TextStyle(
                            color: sel ? Colors.black : kTextSub,
                            fontWeight: FontWeight.w700,
                            fontSize: 13,
                          ),
                        ),
                      ),
                    ),
                  ),
                );
              }).toList(),
        ),
      ),
      SizedBox(height: 16),
      TextField(
        controller: dhanId,
        style: TextStyle(color: kTextPrimary),
        decoration: InputDecoration(
          labelText:
              'Dhan client ID ${mode == 'live' ? '(required)' : '(optional)'}',
        ),
      ),
      SizedBox(height: 12),
      TextField(
        controller: dhanToken,
        obscureText: !showDhanToken,
        style: TextStyle(color: kTextPrimary),
        decoration: InputDecoration(
          labelText:
              'Dhan access token ${mode == 'live' ? '(required)' : '(optional)'}',
          suffixIcon: IconButton(
            icon: Icon(
              showDhanToken
                  ? Icons.visibility_off_outlined
                  : Icons.visibility_outlined,
              color: kTextSub,
              size: 20,
            ),
            onPressed: () => setState(() => showDhanToken = !showDhanToken),
          ),
        ),
      ),
      SizedBox(height: 12),
      TextField(
        controller: aiKey,
        obscureText: !showAiKey,
        style: TextStyle(color: kTextPrimary),
        decoration: InputDecoration(
          labelText: 'AI API key (optional)',
          helperText:
              'Empty = local council. Supplied key = AI + local council.',
          helperStyle: TextStyle(color: kTextSub, fontSize: 10),
          suffixIcon: IconButton(
            icon: Icon(
              showAiKey
                  ? Icons.visibility_off_outlined
                  : Icons.visibility_outlined,
              color: kTextSub,
              size: 20,
            ),
            onPressed: () => setState(() => showAiKey = !showAiKey),
          ),
        ),
      ),
      if (error != null)
        Padding(
          padding: EdgeInsets.only(top: 12),
          child: Container(
            padding: EdgeInsets.all(10),
            decoration: BoxDecoration(
              color: kRed.withValues(alpha: 0.15),
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: kRed.withValues(alpha: 0.5)),
            ),
            child: Text(error!, style: TextStyle(color: kRed, fontSize: 12)),
          ),
        ),
      SizedBox(height: 24),
      yellowButton(
        label: saving ? 'Saving...' : 'SAVE CONFIGURATION',
        icon: Icons.save_rounded,
        onPressed: saving ? null : save,
      ),
    ],
  );
}

// Ã¢â€â‚¬Ã¢â€â‚¬ DASHBOARD PAGE Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
class DashboardPage extends StatefulWidget {
  const DashboardPage({super.key, required this.session});
  final Session session;
  @override
  State<DashboardPage> createState() => _DashboardPageState();
}

class _DashboardPageState extends State<DashboardPage> {
  int _activeSubTab = 0;

  @override
  Widget build(BuildContext context) {
    final data = widget.session.dashboardData;
    final historyList = (data['history'] as List?) ?? [];
    final positionsList = (data['positions_detail'] as List?) ?? [];
    final tradeStats = (data['trade_stats'] as Map<String, dynamic>?) ?? {};
    final ordersList =
        (tradeStats['order_history'] as List?) ??
        (tradeStats['closed_trades'] as List?) ??
        [];
    final isRunning = widget.session.isEngineRunning;
    final regimeStr = (data['regime'] as String?) ?? 'NEUTRAL';
    final regimeScore =
        num.tryParse(data['regime_score']?.toString() ?? '0.5') ?? 0.5;
    final totalVal = num.tryParse(data['total']?.toString() ?? '0') ?? 0;
    final cashVal = num.tryParse(data['cash']?.toString() ?? '0') ?? 0;
    final investedVal =
        num.tryParse(data['invested']?.toString() ?? '0') ??
        (totalVal - cashVal > 0 ? totalVal - cashVal : 0);
    final t1 = num.tryParse(data['tier1_usd']?.toString() ?? '0') ?? 0;
    final t2 = num.tryParse(data['tier2_usd']?.toString() ?? '0') ?? 0;
    final t3 = num.tryParse(data['tier3_usd']?.toString() ?? '0') ?? 0;
    final startVal =
        historyList.isNotEmpty
            ? (num.tryParse(
                  historyList.first['wallet_cap']?.toString() ??
                      historyList.first['total_usd']?.toString() ??
                      totalVal.toString(),
                ) ??
                totalVal)
            : totalVal;
    final sessionPnl = totalVal > 0 && startVal > 0 ? totalVal - startVal : 0.0;
    final sessionPct = startVal > 0 ? (sessionPnl / startVal * 100) : 0.0;
    num openPnl = 0;
    int winnersCount = 0;
    for (final pos in positionsList) {
      final p = pos as Map<String, dynamic>;
      final bp =
          num.tryParse(
            p['buy_price']?.toString() ?? p['entry_price']?.toString() ?? '0',
          ) ??
          0;
      final cp =
          num.tryParse(p['current_price']?.toString() ?? bp.toString()) ?? bp;
      final qty = num.tryParse(p['qty']?.toString() ?? '1') ?? 1;
      final pnl = (cp - bp) * qty;
      openPnl += pnl;
      if (pnl > 0) winnersCount++;
    }
    final wins =
        num.tryParse(tradeStats['wins']?.toString() ?? '0')?.toInt() ?? 0;
    final losses =
        num.tryParse(tradeStats['losses']?.toString() ?? '0')?.toInt() ?? 0;
    final totalTrades = wins + losses;
    final winRatePct = totalTrades > 0 ? (wins / totalTrades * 100) : 0.0;

    return Scaffold(
      backgroundColor: kBgBlack,
      floatingActionButton: FloatingActionButton.extended(
        backgroundColor: isRunning ? kRed : kYellow,
        foregroundColor: isRunning ? Colors.white : Colors.black,
        elevation: 8,
        onPressed: () {
          if (isRunning) {
            widget.session.api.stop().then(
              (_) => widget.session.fetchLatestData(),
            );
          } else {
            showStartBotSheet(context, widget.session);
          }
        },
        icon: Icon(
          isRunning ? Icons.stop_circle_rounded : Icons.play_arrow_rounded,
        ),
        label: Text(
          isRunning ? 'STOP BOT' : 'RUN BOT',
          style: TextStyle(fontWeight: FontWeight.bold, letterSpacing: 1),
        ),
      ),
      body: AnimatedBuilder(
        animation: widget.session,
        builder:
            (_, _) => RefreshIndicator(
              color: kYellow,
              onRefresh: () => widget.session.fetchLatestData(),
              child: ListView(
                padding: EdgeInsets.fromLTRB(14, 16, 14, 80),
                children: [
                  glowBox(
                    glowColor:
                        isRunning ? kGreen.withValues(alpha: 0.5) : kBlueGlow,
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
                                    color: isRunning ? kGreen : kTextSub,
                                    shape: BoxShape.circle,
                                    boxShadow:
                                        isRunning
                                            ? [
                                              BoxShadow(
                                                color: kGreen,
                                                blurRadius: 8,
                                                spreadRadius: 2,
                                              ),
                                            ]
                                            : null,
                                  ),
                                ),
                                SizedBox(width: 8),
                                Text(
                                  isRunning
                                      ? 'LIVE ENGINE ACTIVE'
                                      : 'ENGINE STANDBY',
                                  style: TextStyle(
                                    color: isRunning ? kGreen : kTextSub,
                                    fontFamily: 'monospace',
                                    fontWeight: FontWeight.bold,
                                    fontSize: 12,
                                    letterSpacing: 1.1,
                                  ),
                                ),
                              ],
                            ),
                            Container(
                              padding: EdgeInsets.symmetric(
                                horizontal: 10,
                                vertical: 4,
                              ),
                              decoration: BoxDecoration(
                                color: kBlueGlow.withValues(alpha: 0.18),
                                borderRadius: BorderRadius.circular(8),
                                border: Border.all(
                                  color: kBlueGlow.withValues(alpha: 0.4),
                                ),
                              ),
                              child: Text(
                                'CYCLE #${historyList.isNotEmpty ? (historyList.last['cycle'] ?? historyList.length) : 0}',
                                style: TextStyle(
                                  fontFamily: 'monospace',
                                  color: Color(0xff64b5f6),
                                  fontSize: 11,
                                  fontWeight: FontWeight.bold,
                                ),
                              ),
                            ),
                          ],
                        ),
                        SizedBox(height: 16),
                        Text(
                          'PORTFOLIO VALUE',
                          style: TextStyle(
                            color: kTextSub,
                            fontSize: 10,
                            letterSpacing: 1.2,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                        SizedBox(height: 4),
                        Row(
                          crossAxisAlignment: CrossAxisAlignment.baseline,
                          textBaseline: TextBaseline.alphabetic,
                          children: [
                            Text(
                              'Rs ${totalVal.toStringAsFixed(2)}',
                              style: TextStyle(
                                color: kTextPrimary,
                                fontFamily: 'monospace',
                                fontSize: 28,
                                fontWeight: FontWeight.bold,
                                letterSpacing: -.5,
                              ),
                            ),
                            SizedBox(width: 12),
                            Container(
                              padding: EdgeInsets.symmetric(
                                horizontal: 8,
                                vertical: 4,
                              ),
                              decoration: BoxDecoration(
                                color: (sessionPnl >= 0 ? kGreen : kRed)
                                    .withValues(alpha: 0.15),
                                borderRadius: BorderRadius.circular(6),
                              ),
                              child: Text(
                                '${sessionPnl >= 0 ? '+' : ''}Rs ${sessionPnl.abs().toStringAsFixed(2)} (${sessionPct.toStringAsFixed(2)}%)',
                                style: TextStyle(
                                  color: sessionPnl >= 0 ? kGreen : kRed,
                                  fontFamily: 'monospace',
                                  fontWeight: FontWeight.bold,
                                  fontSize: 12,
                                ),
                              ),
                            ),
                          ],
                        ),
                        SizedBox(height: 12),
                        Container(
                          padding: EdgeInsets.all(10),
                          decoration: BoxDecoration(
                            color: kBgCard2,
                            borderRadius: BorderRadius.circular(10),
                            border: Border.all(color: kBorder),
                          ),
                          child: Text(
                            positionsList.isNotEmpty
                                ? 'Session started Rs ${startVal.toStringAsFixed(2)}. $winnersCount of ${positionsList.length} open stocks above buy price.'
                                : 'No open positions. MaxAlpha scans for gap-ups during NSE market hours.',
                            style: TextStyle(
                              color: kTextSub,
                              fontSize: 11,
                              height: 1.4,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                  SizedBox(height: 12),
                  _buildRegimeBanner(
                    regimeStr,
                    regimeScore,
                    t1,
                    t2,
                    t3,
                    totalVal,
                  ),
                  SizedBox(height: 12),
                  GridView.count(
                    crossAxisCount: 2,
                    childAspectRatio: 1.55,
                    shrinkWrap: true,
                    physics: NeverScrollableScrollPhysics(),
                    crossAxisSpacing: 10,
                    mainAxisSpacing: 10,
                    children: [
                      _metricCard(
                        'CASH AVAILABLE',
                        'Rs ${cashVal.toStringAsFixed(0)}',
                        'Deployable budget',
                        Color(0xff64b5f6),
                      ),
                      _metricCard(
                        'INVESTED',
                        'Rs ${investedVal.toStringAsFixed(0)}',
                        '${positionsList.length} active stocks',
                        kYellow,
                      ),
                      _metricCard(
                        'UNREALIZED P&L',
                        '${openPnl >= 0 ? '+' : ''}Rs ${openPnl.abs().toStringAsFixed(2)}',
                        openPnl >= 0 ? 'Profitable positions' : 'Open drawdown',
                        openPnl >= 0 ? kGreen : kRed,
                      ),
                      _metricCard(
                        'WIN RATE',
                        totalTrades > 0
                            ? '${winRatePct.toStringAsFixed(1)}%'
                            : 'N/A',
                        '$wins wins / $totalTrades trades',
                        winRatePct >= 50 ? kGreen : kYellow,
                      ),
                    ],
                  ),
                  SizedBox(height: 16),
                  Container(
                    height: 44,
                    decoration: BoxDecoration(
                      color: kBgCard,
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(color: kBorder),
                    ),
                    padding: EdgeInsets.all(3),
                    child: Row(
                      children: [
                        _subTabBtn(0, 'P&L'),
                        _subTabBtn(1, 'Positions (${positionsList.length})'),
                        _subTabBtn(2, 'Cycles (${historyList.length})'),
                        _subTabBtn(3, 'Orders (${ordersList.length})'),
                      ],
                    ),
                  ),
                  SizedBox(height: 10),
                  if (_activeSubTab == 0)
                    _buildPnlBreakdown(historyList, startVal),
                  if (_activeSubTab == 1) _buildPositions(positionsList),
                  if (_activeSubTab == 2) _buildCycles(historyList),
                  if (_activeSubTab == 3) _buildOrders(ordersList),
                  SizedBox(height: 14),
                  Text(
                    'TIER STRATEGY ALLOCATION',
                    style: TextStyle(
                      color: kTextSub,
                      fontSize: 10,
                      letterSpacing: 1.2,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  SizedBox(height: 8),
                  _tierCard(
                    'Tier 1 Ã¢â‚¬â€ Momentum Smallcaps',
                    'Rs 20-250 Ã¢â‚¬Â¢ 60% Bull',
                    t1,
                    totalVal,
                    Color(0xffffa726),
                  ),
                  SizedBox(height: 8),
                  _tierCard(
                    'Tier 2 Ã¢â‚¬â€ Midcap Growth',
                    'Rs 250-1500 Ã¢â‚¬Â¢ 30% Bull',
                    t2,
                    totalVal,
                    Color(0xff64b5f6),
                  ),
                  SizedBox(height: 8),
                  _tierCard(
                    'Tier 3 Ã¢â‚¬â€ Bluechip / ETF',
                    'Core Safety Ã¢â‚¬Â¢ 10% Bull',
                    t3,
                    totalVal,
                    kGreen,
                  ),
                  SizedBox(height: 30),
                ],
              ),
            ),
      ),
    );
  }

  Widget _subTabBtn(int index, String label) {
    final sel = _activeSubTab == index;
    return Expanded(
      child: GestureDetector(
        onTap: () => setState(() => _activeSubTab = index),
        child: AnimatedContainer(
          duration: Duration(milliseconds: 180),
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: sel ? kYellow : Colors.transparent,
            borderRadius: BorderRadius.circular(9),
            boxShadow:
                sel
                    ? [
                      BoxShadow(
                        color: kYellow.withValues(alpha: 0.35),
                        blurRadius: 6,
                      ),
                    ]
                    : null,
          ),
          child: Text(
            label,
            style: TextStyle(
              color: sel ? Colors.black : kTextSub,
              fontSize: 10,
              fontWeight: sel ? FontWeight.bold : FontWeight.w600,
            ),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ),
    );
  }

  Widget _metricCard(String label, String value, String sub, Color accent) {
    return glowBox(
      glowColor: kBlueGlow,
      padding: EdgeInsets.all(12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Text(
            label,
            style: TextStyle(
              color: kTextSub,
              fontSize: 9.5,
              fontWeight: FontWeight.bold,
              letterSpacing: 1,
            ),
          ),
          SizedBox(height: 4),
          Text(
            value,
            style: TextStyle(
              color: accent,
              fontFamily: 'monospace',
              fontSize: 16,
              fontWeight: FontWeight.bold,
            ),
          ),
          SizedBox(height: 2),
          Text(
            sub,
            style: TextStyle(color: kTextSub, fontSize: 9.5),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ],
      ),
    );
  }

  Widget _buildRegimeBanner(
    String regime,
    num score,
    num t1,
    num t2,
    num t3,
    num total,
  ) {
    final u = regime.toUpperCase();
    Color fg;
    if (u.contains('BULL')) {
      fg = kGreen;
    } else if (u.contains('BEAR')) {
      fg = kRed;
    } else {
      fg = Color(0xff64b5f6);
    }
    return glowBox(
      glowColor: fg.withValues(alpha: 0.4),
      child: Column(
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Container(
                padding: EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                decoration: BoxDecoration(
                  color: fg.withValues(alpha: 0.15),
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Text(
                  u,
                  style: TextStyle(
                    color: fg,
                    fontFamily: 'monospace',
                    fontWeight: FontWeight.bold,
                    fontSize: 12,
                  ),
                ),
              ),
              Text(
                '${(score * 100).toStringAsFixed(0)}% Score',
                style: TextStyle(
                  color: kTextSub,
                  fontSize: 11,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
          ),
          SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: _tierProg(
                  'T1 Smallcap',
                  t1,
                  total,
                  Color(0xffffa726),
                  0.60,
                ),
              ),
              SizedBox(width: 8),
              Expanded(
                child: _tierProg(
                  'T2 Midcap',
                  t2,
                  total,
                  Color(0xff64b5f6),
                  0.30,
                ),
              ),
              SizedBox(width: 8),
              Expanded(
                child: _tierProg('T3 Bluechip', t3, total, kGreen, 0.10),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _tierProg(
    String label,
    num val,
    num total,
    Color color,
    double target,
  ) {
    final pct = total > 0 ? (val / total).clamp(0.0, 1.0) : 0.0;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(label, style: TextStyle(color: kTextSub, fontSize: 9.5)),
            Text(
              '${(target * 100).toStringAsFixed(0)}%',
              style: TextStyle(
                color: color,
                fontSize: 9.5,
                fontWeight: FontWeight.bold,
              ),
            ),
          ],
        ),
        SizedBox(height: 4),
        ClipRRect(
          borderRadius: BorderRadius.circular(3),
          child: LinearProgressIndicator(
            value: pct.toDouble(),
            color: color,
            backgroundColor: kBgCard2,
            minHeight: 5,
          ),
        ),
      ],
    );
  }

  Widget _buildPnlBreakdown(List historyList, num sessionStartValue) {
    if (historyList.isEmpty) {
      return glowBox(
        glowColor: kBlueGlow,
        child: Center(
          heightFactor: 3,
          child: Text(
            'No cycle data yet. Start bot to generate P&L history.',
            style: TextStyle(color: kTextSub, fontSize: 12),
          ),
        ),
      );
    }
    final reversedHistory = historyList.reversed.toList(growable: false);
    return glowBox(
      glowColor: kBlueGlow,
      child: Column(
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(
                'P&L BREAKDOWN (PER CYCLE)',
                style: TextStyle(
                  color: kTextSub,
                  fontSize: 10,
                  fontWeight: FontWeight.bold,
                  letterSpacing: 1.1,
                ),
              ),
              Container(
                padding: EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  color: kYellow.withValues(alpha: 0.15),
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Text(
                  '${historyList.length} CYCLES',
                  style: TextStyle(
                    color: kYellow,
                    fontSize: 10,
                    fontWeight: FontWeight.bold,
                    fontFamily: 'monospace',
                  ),
                ),
              ),
            ],
          ),
          SizedBox(height: 10),
          SizedBox(
            height: 280,
            child: Scrollbar(
              thumbVisibility: true,
              child: ListView.separated(
                itemCount: reversedHistory.length,
                separatorBuilder: (_, _) => Divider(height: 1, color: kBorder),
                itemBuilder: (ctx, idx) {
                  final item = reversedHistory[idx] as Map<String, dynamic>;
                  final cycleNum = item['cycle'] ?? (historyList.length - idx);
                  final totalUsd =
                      num.tryParse(item['total_usd']?.toString() ?? '0') ?? 0;
                  num prevVal = sessionStartValue;
                  final origIdx = historyList.length - 1 - idx;
                  if (origIdx > 0) {
                    final prev =
                        historyList[origIdx - 1] as Map<String, dynamic>;
                    prevVal =
                        num.tryParse(prev['total_usd']?.toString() ?? '0') ??
                        sessionStartValue;
                  }
                  final pnl = totalUsd - prevVal;
                  final pct = prevVal > 0 ? (pnl / prevVal * 100) : 0.0;
                  final isPos = pnl >= 0;
                  return Padding(
                    padding: EdgeInsets.symmetric(vertical: 8, horizontal: 2),
                    child: Row(
                      children: [
                        SizedBox(
                          width: 64,
                          child: Text(
                            'Cycle $cycleNum',
                            style: TextStyle(
                              color: kTextSub,
                              fontSize: 11,
                              fontFamily: 'monospace',
                            ),
                          ),
                        ),
                        Expanded(
                          flex: 3,
                          child: Text(
                            '${isPos ? '+' : ''}Rs ${pnl.abs().toStringAsFixed(2)}',
                            style: TextStyle(
                              color: isPos ? kGreen : kRed,
                              fontFamily: 'monospace',
                              fontWeight: FontWeight.bold,
                              fontSize: 11.5,
                            ),
                          ),
                        ),
                        Expanded(
                          flex: 3,
                          child: Text(
                            'Rs ${totalUsd.toStringAsFixed(0)}',
                            style: TextStyle(
                              color: kTextPrimary,
                              fontFamily: 'monospace',
                              fontSize: 11,
                            ),
                          ),
                        ),
                        SizedBox(
                          width: 48,
                          child: Container(
                            height: 4,
                            decoration: BoxDecoration(
                              color: kBgCard2,
                              borderRadius: BorderRadius.circular(2),
                            ),
                            child: FractionallySizedBox(
                              alignment: Alignment.centerLeft,
                              widthFactor: (pct.abs() / 2.0).clamp(0.05, 1.0),
                              child: Container(
                                decoration: BoxDecoration(
                                  color: isPos ? kGreen : kRed,
                                  borderRadius: BorderRadius.circular(2),
                                ),
                              ),
                            ),
                          ),
                        ),
                        SizedBox(width: 6),
                        SizedBox(
                          width: 52,
                          child: Text(
                            '${isPos ? '+' : ''}${pct.toStringAsFixed(2)}%',
                            textAlign: TextAlign.end,
                            style: TextStyle(
                              color: isPos ? kGreen : kRed,
                              fontSize: 10,
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

  Widget _buildPositions(List positionsList) {
    if (positionsList.isEmpty) {
      return glowBox(
        glowColor: kBlueGlow,
        child: Center(
          heightFactor: 3,
          child: Text(
            'No active positions.',
            style: TextStyle(color: kTextSub, fontSize: 12),
          ),
        ),
      );
    }
    return glowBox(
      glowColor: kBlueGlow,
      padding: EdgeInsets.all(8),
      child: SizedBox(
        height: 420,
        child: ListView.separated(
          itemCount: positionsList.length,
          separatorBuilder: (_, _) => SizedBox(height: 10),
          itemBuilder: (ctx, idx) {
            final p = positionsList[idx] as Map<String, dynamic>;
            final sym =
                p['ticker']?.toString() ?? p['symbol']?.toString() ?? 'STOCK';
            final qty = p['qty'] ?? p['shares'] ?? 0;
            final buyPx =
                num.tryParse(
                  p['buy_price']?.toString() ??
                      p['entry_price']?.toString() ??
                      '0',
                ) ??
                0;
            final curPx =
                num.tryParse(
                  p['current_price']?.toString() ?? buyPx.toString(),
                ) ??
                buyPx;
            final pnlPct = num.tryParse(p['pnl_pct']?.toString() ?? '0') ?? 0;
            final profitRs =
                (curPx - buyPx) * (num.tryParse(qty.toString()) ?? 1);
            final canSell = profitRs > 0;
            final winProb =
                num.tryParse(p['win_probability']?.toString() ?? '0') ?? 0;
            final tier = p['tier'] ?? 1;
            return glowBox(
              glowColor: canSell ? kGreen.withValues(alpha: 0.4) : kBlueGlow,
              child: Column(
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Container(
                        padding: EdgeInsets.symmetric(
                          horizontal: 8,
                          vertical: 4,
                        ),
                        decoration: BoxDecoration(
                          color: kYellow.withValues(alpha: 0.15),
                          borderRadius: BorderRadius.circular(6),
                        ),
                        child: Text(
                          'T$tier $sym',
                          style: TextStyle(
                            fontFamily: 'monospace',
                            fontWeight: FontWeight.bold,
                            fontSize: 13,
                            color: kYellow,
                          ),
                        ),
                      ),
                      Container(
                        padding: EdgeInsets.symmetric(
                          horizontal: 8,
                          vertical: 3,
                        ),
                        decoration: BoxDecoration(
                          color: (canSell ? kGreen : kRed).withValues(
                            alpha: 0.15,
                          ),
                          borderRadius: BorderRadius.circular(6),
                        ),
                        child: Text(
                          canSell ? 'SELL OK' : 'HOLDING',
                          style: TextStyle(
                            color: canSell ? kGreen : kRed,
                            fontSize: 9.5,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                      ),
                    ],
                  ),
                  SizedBox(height: 12),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            'BUY PRICE',
                            style: TextStyle(color: kTextSub, fontSize: 9.5),
                          ),
                          Text(
                            'Rs ${buyPx.toStringAsFixed(2)}',
                            style: TextStyle(
                              fontFamily: 'monospace',
                              fontWeight: FontWeight.w600,
                              fontSize: 12,
                              color: kTextPrimary,
                            ),
                          ),
                        ],
                      ),
                      Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            'CURRENT',
                            style: TextStyle(color: kTextSub, fontSize: 9.5),
                          ),
                          Text(
                            'Rs ${curPx.toStringAsFixed(2)}',
                            style: TextStyle(
                              fontFamily: 'monospace',
                              fontWeight: FontWeight.w600,
                              fontSize: 12,
                              color: kTextPrimary,
                            ),
                          ),
                        ],
                      ),
                      Column(
                        crossAxisAlignment: CrossAxisAlignment.end,
                        children: [
                          Text(
                            'UNREALIZED P&L',
                            style: TextStyle(color: kTextSub, fontSize: 9.5),
                          ),
                          Text(
                            '${profitRs >= 0 ? '+' : ''}Rs ${profitRs.abs().toStringAsFixed(2)} (${pnlPct >= 0 ? '+' : ''}${pnlPct.toStringAsFixed(2)}%)',
                            style: TextStyle(
                              color: profitRs >= 0 ? kGreen : kRed,
                              fontFamily: 'monospace',
                              fontWeight: FontWeight.bold,
                              fontSize: 12,
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),
                  if (winProb > 0) ...[
                    SizedBox(height: 8),
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Text(
                          'Est. Win Chance: ${winProb.toStringAsFixed(1)}%',
                          style: TextStyle(color: kTextSub, fontSize: 10.5),
                        ),
                        ClipRRect(
                          borderRadius: BorderRadius.circular(4),
                          child: SizedBox(
                            width: 100,
                            child: LinearProgressIndicator(
                              value: (winProb / 100).clamp(0.0, 1.0),
                              color: winProb >= 50 ? kGreen : kYellow,
                              backgroundColor: kBgCard2,
                              minHeight: 4,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ],
                ],
              ),
            );
          },
        ),
      ),
    );
  }

  Widget _buildCycles(List historyList) {
    if (historyList.isEmpty) {
      return glowBox(
        glowColor: kBlueGlow,
        child: Center(
          heightFactor: 3,
          child: Text(
            'No cycle logs yet.',
            style: TextStyle(color: kTextSub, fontSize: 12),
          ),
        ),
      );
    }
    final reversedHistory = historyList.reversed.toList(growable: false);
    return glowBox(
      glowColor: kBlueGlow,
      padding: EdgeInsets.all(8),
      child: SizedBox(
        height: 300,
        child: Scrollbar(
          thumbVisibility: true,
          child: ListView.separated(
            itemCount: reversedHistory.length,
            separatorBuilder: (_, _) => Divider(height: 1, color: kBorder),
            itemBuilder: (ctx, idx) {
            final item = reversedHistory[idx] as Map<String, dynamic>;
            final cycle = item['cycle'] ?? (historyList.length - idx);
            final tot = num.tryParse(item['total_usd']?.toString() ?? '0') ?? 0;
            final reg = item['regime']?.toString() ?? 'NEUTRAL';
            final sigs = item['signals'] ?? 0;
            final ts = item['ts']?.toString() ?? '';
            String timeStr = '--';
            if (ts.isNotEmpty) {
              try {
                final dt = DateTime.parse(ts).toLocal();
                timeStr =
                    '${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}';
              } catch (_) {}
            }
            return ListTile(
              dense: true,
              leading: Text(
                'C#$cycle',
                style: TextStyle(
                  fontFamily: 'monospace',
                  color: kYellow,
                  fontWeight: FontWeight.bold,
                  fontSize: 11,
                ),
              ),
              title: Text(
                'Portfolio: Rs ${tot.toStringAsFixed(0)}',
                style: TextStyle(
                  fontFamily: 'monospace',
                  fontWeight: FontWeight.w600,
                  fontSize: 12,
                  color: kTextPrimary,
                ),
              ),
              subtitle: Text(
                'Regime: $reg Ã¢â‚¬Â¢ Time: $timeStr',
                style: TextStyle(color: kTextSub, fontSize: 10),
              ),
              trailing: Text(
                '$sigs Signals',
                style: TextStyle(
                  color: sigs > 0 ? kGreen : kTextSub,
                  fontSize: 10,
                  fontWeight: FontWeight.bold,
                ),
              ),
            );
            },
          ),
        ),
      ),
    );
  }

  Widget _buildOrders(List ordersList) {
    if (ordersList.isEmpty) {
      return glowBox(
        glowColor: kBlueGlow,
        child: Center(
          heightFactor: 3,
          child: Text(
            'No orders yet.',
            style: TextStyle(color: kTextSub, fontSize: 12),
          ),
        ),
      );
    }
    final reversedOrders = ordersList.reversed.toList(growable: false);
    return glowBox(
      glowColor: kBlueGlow,
      padding: EdgeInsets.all(8),
      child: SizedBox(
        height: 300,
        child: Scrollbar(
          thumbVisibility: true,
          child: ListView.separated(
            itemCount: reversedOrders.length,
            separatorBuilder: (_, _) => Divider(height: 1, color: kBorder),
            itemBuilder: (ctx, idx) {
            final order = reversedOrders[idx] as Map<String, dynamic>;
            final side =
                (order['event']?.toString() ??
                        (order['sell_price'] != null ? 'SELL' : 'BUY'))
                    .toUpperCase();
            final tkr = order['ticker']?.toString() ?? '--';
            final qty = order['qty'] ?? 0;
            final px =
                num.tryParse(
                  order['price']?.toString() ??
                      order['buy_price']?.toString() ??
                      '0',
                ) ??
                0;
            final pnl = num.tryParse(order['pnl']?.toString() ?? '0') ?? 0;
            return ListTile(
              dense: true,
              leading: Container(
                padding: EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  color: (side == 'BUY' ? Color(0xff64b5f6) : kGreen)
                      .withValues(alpha: 0.18),
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Text(
                  side,
                  style: TextStyle(
                    color: side == 'BUY' ? Color(0xff64b5f6) : kGreen,
                    fontSize: 10,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ),
              title: Text(
                '$tkr (Qty: $qty @ Rs ${px.toStringAsFixed(1)})',
                style: TextStyle(
                  fontFamily: 'monospace',
                  fontWeight: FontWeight.w600,
                  fontSize: 12,
                  color: kTextPrimary,
                ),
              ),
              subtitle: Text(
                order['reason']?.toString() ??
                    order['strategy']?.toString() ??
                    'Cycle signal executed',
                style: TextStyle(color: kTextSub, fontSize: 10),
              ),
              trailing:
                  side == 'SELL'
                      ? Text(
                        '${pnl >= 0 ? '+' : ''}Rs ${pnl.abs().toStringAsFixed(2)}',
                        style: TextStyle(
                          color: pnl >= 0 ? kGreen : kRed,
                          fontFamily: 'monospace',
                          fontWeight: FontWeight.bold,
                          fontSize: 11,
                        ),
                      )
                      : null,
            );
            },
          ),
        ),
      ),
    );
  }

  Widget _tierCard(
    String title,
    String target,
    num val,
    num total,
    Color color,
  ) {
    final pct = total > 0 ? (val / total * 100) : 0.0;
    return glowBox(
      glowColor: color.withValues(alpha: 0.3),
      child: Column(
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(
                title,
                style: TextStyle(
                  color: kTextPrimary,
                  fontWeight: FontWeight.w600,
                  fontSize: 12,
                ),
              ),
              Text(
                'Rs ${val.toStringAsFixed(0)} (${pct.toStringAsFixed(1)}%)',
                style: TextStyle(
                  color: color,
                  fontFamily: 'monospace',
                  fontWeight: FontWeight.bold,
                  fontSize: 12,
                ),
              ),
            ],
          ),
          SizedBox(height: 3),
          Text(target, style: TextStyle(color: kTextSub, fontSize: 10)),
          SizedBox(height: 6),
          ClipRRect(
            borderRadius: BorderRadius.circular(3),
            child: LinearProgressIndicator(
              value: (pct / 100).clamp(0.0, 1.0),
              color: color,
              backgroundColor: kBgCard2,
              minHeight: 5,
            ),
          ),
        ],
      ),
    );
  }
}

// Ã¢â€â‚¬Ã¢â€â‚¬ EXACT DASHBOARD (HTML WEBVIEW) PAGE Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
class ExactDashboardPage extends StatefulWidget {
  const ExactDashboardPage({super.key, required this.session});
  final Session session;
  @override
  State<ExactDashboardPage> createState() => _ExactDashboardPageState();
}

class _ExactDashboardPageState extends State<ExactDashboardPage> {
  late final WebViewController _controller;
  bool? _appliedDarkTheme;
  String? _loadedDashboardUrl;
  bool _dashboardLoaded = false;
  String _lastSyncedSignature = '';

  @override
  void initState() {
    super.initState();
    _controller =
        WebViewController()
          ..setJavaScriptMode(JavaScriptMode.unrestricted)
          ..setNavigationDelegate(
            NavigationDelegate(
              onNavigationRequest:
                  (request) =>
                      request.url.startsWith('http://127.0.0.1:8765/')
                          ? NavigationDecision.navigate
                          : NavigationDecision.prevent,
              onPageFinished: (_) => _openDashboard(),
            ),
          );
    widget.session.addListener(_loadDashboardWhenTargetChanges);
    widget.session.addListener(_syncDashboardWhenSessionChanges);
    _loadDashboard();
  }

  void _loadDashboardWhenTargetChanges() {
    if (!_dashboardLoaded || _loadedDashboardUrl != widget.session.dashboardUrl) {
      _loadDashboard();
    }
  }

  void _syncDashboardWhenSessionChanges() {
    if (!_dashboardLoaded || !mounted) return;
    final signature = _sessionSignature();
    if (signature == _lastSyncedSignature) return;
    _lastSyncedSignature = signature;
    _syncDashboardFromSession();
  }

  String _sessionSignature() {
    final data = widget.session.dashboardData;
    final history = (data['history'] as List?) ?? const [];
    final positions = (data['positions_detail'] as List?) ?? const [];
    final tradeStats = (data['trade_stats'] as Map<String, dynamic>?) ?? const {};
    return [
      data['cycle']?.toString() ?? '',
      history.length.toString(),
      positions.length.toString(),
      tradeStats['wins']?.toString() ?? '',
      tradeStats['losses']?.toString() ?? '',
      widget.session.logLines.length.toString(),
      widget.session.isEngineRunning.toString(),
    ].join('|');
  }

  Future<void> _syncDashboardFromSession() async {
    try {
      final history = jsonEncode(
        (widget.session.dashboardData['history'] as List?) ?? const [],
      );
      await _controller.runJavaScript('''
        (function() {
          if ($history !== null) window.__maxAlphaHistory = $history;
          if (typeof loadAll === 'function') loadAll();
          if (typeof loadPerformanceLog === 'function') loadPerformanceLog();
          if (typeof updateAgentStatus === 'function') updateAgentStatus();
        })();
      ''');
    } catch (_) {}
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
      final isHttpMode = _loadedDashboardUrl != null;
      final history =
          isHttpMode
              ? 'null'
              : jsonEncode(
                (await widget.session.api.dashboard())['history'] ?? [],
              );
      await _controller.runJavaScript('''
        (function() {
          h = document.querySelector('.hdr');
          if (h) h.style.display = 'none';
          loginScreen = document.getElementById('ls');
          app = document.getElementById('app');
          if (loginScreen) loginScreen.style.display = 'none';
          if (app) app.style.display = 'block';
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
      await _applyDashboardTheme(
        Theme.of(context).brightness == Brightness.dark,
      );
    } catch (_) {}
  }

  Future<void> _applyDashboardTheme(bool dark) async {
    _appliedDarkTheme = dark;
    final values =
        dark
            ? '--bg:#000000;--s1:#0c0c0c;--s2:#121212;--s3:#181818;--b1:rgba(255,255,255,.08);--b2:rgba(255,255,255,.14);--b3:rgba(255,214,0,.28);--tx:#edf5ff;--mu:#6f88aa;--mu2:#9fb4d0;--am:#ffd600;--ad:rgba(255,214,0,.15);'
            : '--bg:#fffdf7;--s1:#ffffff;--s2:#fff8dc;--s3:#fff1b8;--b1:rgba(23,23,23,.12);--b2:rgba(23,23,23,.22);--b3:rgba(184,134,11,.42);--tx:#171717;--mu:#665c36;--mu2:#4a4a4a;--am:#b88600;--ad:rgba(255,214,0,.22);';
    await _controller.runJavaScript(
      "document.documentElement.style.cssText += '$values';",
    );
  }

  @override
  void dispose() {
    widget.session.removeListener(_loadDashboardWhenTargetChanges);
    widget.session.removeListener(_syncDashboardWhenSessionChanges);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    if (_appliedDarkTheme != dark) {
      WidgetsBinding.instance.addPostFrameCallback(
        (_) => _applyDashboardTheme(dark),
      );
    }
    return WebViewWidget(controller: _controller);
  }
}

// Ã¢â€â‚¬Ã¢â€â‚¬ RUN PAGE Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
class RunPage extends StatefulWidget {
  const RunPage({super.key, required this.session});
  final Session session;
  @override
  State<RunPage> createState() => _RunPageState();
}

class _RunPageState extends State<RunPage> {
  bool waiting = false, startingDashboard = false, stoppingDashboard = false;
  bool dashboardRunning = false;
  List<String> get _lines => widget.session.logLines;
  bool get _running => widget.session.isEngineRunning;

  Future<void> _start() async {
    await showStartBotSheet(context, widget.session);
  }

  Future<void> _stop() async {
    setState(() => waiting = true);
    try {
      await widget.session.api.stop();
      await widget.session.fetchLatestData();
    } finally {
      if (mounted) setState(() => waiting = false);
    }
  }

  Future<void> _startDashboard() async {
    setState(() => startingDashboard = true);
    try {
      final response = await widget.session.api.startDashboard();
      final url = response['url']?.toString().trim();
      final dashboardUrl =
          (url == null || url.isEmpty)
              ? 'http://127.0.0.1:8765/dashboard_v5_bot2.html'
              : url;
      widget.session.setDashboardUrl(dashboardUrl);
      if (mounted) setState(() => dashboardRunning = true);
      _notice('Dashboard started. Open HTML View from drawer.');
    } on ApiError catch (e) {
      _notice(e.message);
    } finally {
      if (mounted) setState(() => startingDashboard = false);
    }
  }

  Future<void> _stopDashboard() async {
    setState(() => stoppingDashboard = true);
    try {
      widget.session.setDashboardUrl(null);
      if (mounted) setState(() => dashboardRunning = false);
      _notice('Dashboard stopped.');
    } finally {
      if (mounted) setState(() => stoppingDashboard = false);
    }
  }

  void _notice(String msg) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Color _lineColor(String line) {
    final l = line.toLowerCase();
    if (l.contains('error') || l.contains('exception')) return kRed;
    if (l.contains('warn')) return kYellow;
    if (l.contains('bought') || l.contains('buy')) return Color(0xff64b5f6);
    if (l.contains('sold') || l.contains('sell')) return kGreen;
    return kTextPrimary;
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: widget.session,
      builder:
          (_, _) => Padding(
            padding: EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Status badge
                glowBox(
                  glowColor:
                      _running ? kGreen.withValues(alpha: 0.5) : kBlueGlow,
                  padding: EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                  child: Row(
                    children: [
                      Container(
                        width: 10,
                        height: 10,
                        decoration: BoxDecoration(
                          color: _running ? kGreen : kTextSub,
                          shape: BoxShape.circle,
                          boxShadow:
                              _running
                                  ? [
                                    BoxShadow(
                                      color: kGreen,
                                      blurRadius: 8,
                                      spreadRadius: 1,
                                    ),
                                  ]
                                  : null,
                        ),
                      ),
                      SizedBox(width: 10),
                      Text(
                        _running
                            ? 'ENGINE ACTIVE  Bot is trading'
                            : 'ENGINE STANDBY  Ready to launch',
                        style: TextStyle(
                          color: _running ? kGreen : kTextSub,
                          fontFamily: 'monospace',
                          fontWeight: FontWeight.bold,
                          fontSize: 12,
                          letterSpacing: 0.8,
                        ),
                      ),
                    ],
                  ),
                ),
                SizedBox(height: 10),
                // Main action button with loading spinner
                _loadingButton(
                  loading: waiting,
                  isDestructive: _running,
                  label: _running ? 'STOP ENGINE' : 'START MAXALPHA',
                  icon:
                      _running
                          ? Icons.stop_circle_rounded
                          : Icons.play_arrow_rounded,
                  onPressed: waiting ? null : (_running ? _stop : _start),
                ),
                SizedBox(height: 10),
                // Secondary controls
                Row(
                  children: [
                    Expanded(flex: 2, child: _dashboardButton()),
                    SizedBox(width: 10),
                    GestureDetector(
                      onTap: () => widget.session.fetchLatestData(),
                      child: Container(
                        height: 44,
                        width: 44,
                        decoration: BoxDecoration(
                          color: kBgCard2,
                          borderRadius: BorderRadius.circular(12),
                          border: Border.all(color: kBorder),
                        ),
                        child: Icon(
                          Icons.refresh_rounded,
                          color: kTextSub,
                          size: 20,
                        ),
                      ),
                    ),
                  ],
                ),
                SizedBox(height: 16),
                // Terminal label
                Row(
                  children: [
                    Icon(Icons.terminal_rounded, color: kYellow, size: 16),
                    SizedBox(width: 8),
                    Text(
                      'PYTHON LOGS',
                      style: TextStyle(
                        color: kYellow,
                        fontFamily: 'monospace',
                        fontWeight: FontWeight.bold,
                        fontSize: 11,
                        letterSpacing: 1.2,
                        shadows: [Shadow(color: kYellowGlow, blurRadius: 8)],
                      ),
                    ),
                    Spacer(),
                    Text(
                      '${_lines.length} lines',
                      style: TextStyle(
                        color: kTextSub,
                        fontSize: 10,
                        fontFamily: 'monospace',
                      ),
                    ),
                  ],
                ),
                SizedBox(height: 8),
                // Terminal window Ã¢â‚¬â€ half screen height
                Expanded(
                  child: Container(
                    decoration: BoxDecoration(
                      color:
                          widget.session.dark ? Color(0xff080808) : kBgCard2,
                      borderRadius: BorderRadius.circular(14),
                      border: Border.all(
                        color:
                            widget.session.dark
                                ? kYellow.withValues(alpha: 0.3)
                                : kBorder,
                        width: 1.2,
                      ),
                      boxShadow: [
                        BoxShadow(
                          color:
                              widget.session.dark
                                  ? kYellow.withValues(alpha: 0.08)
                                  : Colors.black.withValues(alpha: 0.04),
                          blurRadius: 12,
                        ),
                      ],
                    ),
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(14),
                      child:
                          _lines.isEmpty
                              ? Center(
                                child: Column(
                                  mainAxisSize: MainAxisSize.min,
                                  children: [
                                    Container(
                                      padding: EdgeInsets.all(14),
                                      decoration: BoxDecoration(
                                        color: kYellow.withValues(alpha: 0.1),
                                        borderRadius: BorderRadius.circular(16),
                                        border: Border.all(
                                          color: kYellow.withValues(alpha: 0.3),
                                        ),
                                      ),
                                      child: Icon(
                                        Icons.terminal_rounded,
                                        color: kYellow,
                                        size: 32,
                                      ),
                                    ),
                                    SizedBox(height: 14),
                                    Text(
                                      'No activity yet',
                                      style: TextStyle(
                                        color: kTextPrimary,
                                        fontWeight: FontWeight.w700,
                                        fontSize: 14,
                                      ),
                                    ),
                                    SizedBox(height: 5),
                                    Text(
                                      'Start MaxAlpha to stream Python logs.',
                                      style: TextStyle(
                                        color: kTextSub,
                                        fontFamily: 'monospace',
                                        fontSize: 11,
                                      ),
                                    ),
                                  ],
                                ),
                              )
                              : ListView.builder(
                                padding: EdgeInsets.all(10),
                                itemCount: _lines.length,
                                itemBuilder: (ctx, idx) {
                                  final line = _lines[idx];
                                  return Padding(
                                    padding: EdgeInsets.only(bottom: 2),
                                    child: Row(
                                      crossAxisAlignment:
                                          CrossAxisAlignment.start,
                                      children: [
                                        SizedBox(
                                          width: 28,
                                          child: Text(
                                            '${idx + 1}',
                                            style: TextStyle(
                                                color:
                                                    widget.session.dark
                                                        ? Color(0xff6f6f6f)
                                                        : Color(0xff665c36),
                                              fontFamily: 'monospace',
                                              fontSize: 10,
                                            ),
                                          ),
                                        ),
                                        Expanded(
                                          child: Text(
                                            line,
                                            style: TextStyle(
                                              color: _lineColor(line),
                                              fontFamily: 'monospace',
                                              fontSize: 11,
                                              height: 1.4,
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
                ),
                SizedBox(height: 8),
              ],
            ),
          ),
    );
  }

  // Loading-aware primary button
  Widget _loadingButton({
    required bool loading,
    required String label,
    required IconData icon,
    required VoidCallback? onPressed,
    bool isDestructive = false,
  }) {
    final bg = isDestructive ? kRed : kYellow;
    final fg = isDestructive ? Colors.white : Colors.black;
    return AnimatedOpacity(
      duration: Duration(milliseconds: 200),
      opacity: onPressed == null && !loading ? 0.4 : 1.0,
      child: GestureDetector(
        onTap: loading ? null : onPressed,
        child: Container(
          height: 44,
          decoration: BoxDecoration(
            gradient: LinearGradient(
              colors:
                  isDestructive
                      ? [kRed, Color(0xffd32f2f)]
                      : [kYellow, kYellowDim],
            ),
            borderRadius: BorderRadius.circular(12),
            boxShadow: [
              BoxShadow(
                color: bg.withValues(alpha: 0.25),
                blurRadius: 6,
                spreadRadius: 0,
                offset: Offset(0, 2),
              ),
            ],
          ),
          child:
              loading
                  ? Center(
                    child: SizedBox(
                      width: 20,
                      height: 20,
                      child: CircularProgressIndicator(
                        strokeWidth: 2.2,
                        color: fg,
                      ),
                    ),
                  )
                  : Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Icon(icon, color: fg, size: 18),
                      SizedBox(width: 8),
                      Text(
                        label,
                        style: TextStyle(
                          color: fg,
                          fontWeight: FontWeight.w800,
                          fontSize: 13,
                          letterSpacing: 0.5,
                        ),
                      ),
                    ],
                  ),
        ),
      ),
    );
  }

  // Dashboard start/stop toggle button
  Widget _dashboardButton() {
    final busy = startingDashboard || stoppingDashboard;
    if (dashboardRunning) {
      // Stop dashboard  red outline
      return AnimatedOpacity(
        duration: Duration(milliseconds: 200),
        opacity: busy ? 0.5 : 1.0,
        child: GestureDetector(
          onTap: busy ? null : _stopDashboard,
          child: Container(
            height: 44,
            decoration: BoxDecoration(
              color: kRed.withValues(alpha: 0.12),
              borderRadius: BorderRadius.circular(12),
              border: Border.all(
                color: kRed.withValues(alpha: 0.6),
                width: 1.2,
              ),
            ),
            child:
                busy
                    ? Center(
                      child: SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          color: kRed,
                        ),
                      ),
                    )
                    : Row(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Icon(Icons.stop_rounded, color: kRed, size: 18),
                        SizedBox(width: 7),
                        Text(
                          'Stop Dashboard',
                          style: TextStyle(
                            color: kRed,
                            fontWeight: FontWeight.w700,
                            fontSize: 12.5,
                          ),
                        ),
                      ],
                    ),
          ),
        ),
      );
    }
    // Start dashboard  grey outline with spinner when busy
    return AnimatedOpacity(
      duration: Duration(milliseconds: 200),
      opacity: busy ? 0.5 : 1.0,
      child: GestureDetector(
        onTap: busy ? null : _startDashboard,
        child: Container(
          height: 44,
          decoration: BoxDecoration(
            color: kBgCard2,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: kBorder, width: 1.2),
          ),
          child:
              busy
                  ? Center(
                    child: SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(
                        strokeWidth: 2,
                        color: kYellow,
                      ),
                    ),
                  )
                  : Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Icon(Icons.dashboard_outlined, color: kTextSub, size: 18),
                      SizedBox(width: 7),
                      Text(
                        'Start Dashboard',
                        style: TextStyle(
                          color: kTextSub,
                          fontWeight: FontWeight.w700,
                          fontSize: 12.5,
                        ),
                      ),
                    ],
                  ),
        ),
      ),
    );
  }
}

// Ã¢â€â‚¬Ã¢â€â‚¬ SETTINGS PAGE Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
class SettingsPage extends StatelessWidget {
  const SettingsPage({super.key, required this.session, required this.onLogout});
  final Session session;
  final VoidCallback onLogout;

  Future<void> _signals(BuildContext context) async {
    try {
      final data = await session.api.signals();
      if (!context.mounted) return;
      await showDialog<void>(
        context: context,
        builder:
            (ctx) => AlertDialog(
              backgroundColor: kBgCard,
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(18),
                side: BorderSide(color: kBlueGlow, width: 1.2),
              ),
              title: Text(
                'Paper Signals',
                style: TextStyle(color: kYellow, fontWeight: FontWeight.bold),
              ),
              content: SingleChildScrollView(
                child: SelectableText(
                  data['content'].toString(),
                  style: TextStyle(
                    fontFamily: 'monospace',
                    fontSize: 11,
                    color: kTextPrimary,
                  ),
                ),
              ),
              actions: [
                GestureDetector(
                  onTap: () => Navigator.pop(ctx),
                  child: Container(
                    padding: EdgeInsets.symmetric(horizontal: 20, vertical: 10),
                    decoration: BoxDecoration(
                      color: kYellow,
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: Text(
                      'Close',
                      style: TextStyle(
                        color: Colors.black,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ),
                ),
              ],
            ),
      );
    } on ApiError catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(e.message)));
      }
    }
  }

  Future<void> _clearHistory(BuildContext context) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder:
          (ctx) => AlertDialog(
            backgroundColor: kBgCard,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(18),
              side: BorderSide(color: kRed.withValues(alpha: 0.6), width: 1.2),
            ),
            title: Text(
              'Clear Bot History?',
              style: TextStyle(color: kRed, fontWeight: FontWeight.bold),
            ),
            content: Text(
              'This will delete performance_v4.json and paper_signals.json.\n\nThis action cannot be undone.',
              style: TextStyle(color: kTextSub, fontSize: 13, height: 1.5),
            ),
            actions: [
              GestureDetector(
                onTap: () => Navigator.pop(ctx, false),
                child: Padding(
                  padding: EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                  child: Text(
                    'Cancel',
                    style: TextStyle(
                      color: kTextSub,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
              ),
              GestureDetector(
                onTap: () => Navigator.pop(ctx, true),
                child: Container(
                  padding: EdgeInsets.symmetric(horizontal: 20, vertical: 10),
                  decoration: BoxDecoration(
                    color: kRed,
                    borderRadius: BorderRadius.circular(10),
                  ),
                  child: Text(
                    'Clear All',
                    style: TextStyle(
                      color: Colors.white,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
              ),
            ],
          ),
    );
    if (confirmed != true || !context.mounted) return;
    try {
      final result = await session.api.clearHistory();
      final cleared = (result['cleared'] as List?)?.join(', ') ?? 'none';
      if (context.mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('Cleared: $cleared')));
      }
    } on ApiError catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(e.message)));
      }
    }
  }

  Widget _tile(
    BuildContext context, {
    required IconData icon,
    required String title,
    required String subtitle,
    required VoidCallback onTap,
    Color? iconColor,
  }) {
    final effectiveIconColor = iconColor ?? kYellow;
    return Padding(
      padding: EdgeInsets.only(bottom: 10),
      child: GestureDetector(
        onTap: onTap,
        child: glowBox(
          glowColor: kBlueGlow,
          child: Row(
            children: [
              Container(
                padding: EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: effectiveIconColor.withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(10),
                  border: Border.all(
                    color: effectiveIconColor.withValues(alpha: 0.3),
                  ),
                ),
                child: Icon(icon, color: effectiveIconColor, size: 20),
              ),
              SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      title,
                      style: TextStyle(
                        color: kTextPrimary,
                        fontWeight: FontWeight.w700,
                        fontSize: 14,
                      ),
                    ),
                    SizedBox(height: 3),
                    Text(
                      subtitle,
                      style: TextStyle(color: kTextSub, fontSize: 11),
                    ),
                  ],
                ),
              ),
              Icon(Icons.chevron_right_rounded, color: kTextSub, size: 20),
            ],
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) => ListView(
    padding: EdgeInsets.fromLTRB(16, 16, 16, 100),
    children: [
      sectionHeading('CONTROL ROOM'),
      Text(
        'Appearance, credentials, and local records',
        style: TextStyle(color: kTextSub, fontSize: 12),
      ),
      SizedBox(height: 20),
      glowBox(
        glowColor: kBlueGlow,
        child: Row(
          children: [
            Container(
              padding: EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: kYellow.withValues(alpha: 0.12),
                borderRadius: BorderRadius.circular(10),
                border: Border.all(color: kYellow.withValues(alpha: 0.3)),
              ),
              child: Icon(Icons.dark_mode_rounded, color: kYellow, size: 20),
            ),
            SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    session.dark ? 'Dark Theme' : 'Light Theme',
                    style: TextStyle(
                      color: kTextPrimary,
                      fontWeight: FontWeight.w700,
                      fontSize: 14,
                    ),
                  ),
                  SizedBox(height: 3),
                  Text(
                    session.dark
                        ? 'Black trading-console theme'
                        : 'White and yellow trading-console theme',
                    style: TextStyle(color: kTextSub, fontSize: 11),
                  ),
                ],
              ),
            ),
            Switch(
              activeThumbColor: kYellow,
              activeTrackColor: kYellow.withValues(alpha: 0.4),
              value: session.dark,
              onChanged: session.toggleTheme,
            ),
          ],
        ),
      ),
      SizedBox(height: 10),
      _tile(
        context,
        icon: Icons.tune_rounded,
        title: 'Edit Bot Credentials',
        subtitle: 'Update paper/live mode, Dhan, or AI key',
        onTap: () {
          Navigator.push(
            context,
            MaterialPageRoute(
              builder:
                  (_) => Scaffold(
                    backgroundColor: kBgBlack,
                    appBar: AppBar(
                      title: Text('Edit Configuration'),
                      backgroundColor: kBgBlack,
                      leading: IconButton(
                        icon: Icon(Icons.arrow_back_rounded, color: kYellow),
                        onPressed: () => Navigator.pop(context),
                      ),
                    ),
                    body: SetupPage(
                      session: session,
                      onComplete: () => Navigator.pop(context),
                    ),
                  ),
            ),
          );
        },
      ),
      _tile(
        context,
        icon: Icons.description_outlined,
        title: 'View Signals JSON',
        subtitle: 'Read paper signals recorded on this device',
        onTap: () => _signals(context),
      ),
      _tile(
        context,
        icon: Icons.delete_sweep_rounded,
        title: 'Clear Bot History',
        subtitle: 'Delete performance_v4.json and paper signals',
        onTap: () => _clearHistory(context),
        iconColor: kRed,
      ),
      _tile(
        context,
        icon: Icons.logout_rounded,
        title: 'Log Out',
        subtitle: 'End your current session',
        onTap: () async {
          await session.signOut();
          onLogout();
        },
        iconColor: kRed,
      ),
    ],
  );
}
