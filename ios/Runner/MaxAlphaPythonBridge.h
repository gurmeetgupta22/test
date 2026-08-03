#import <Flutter/Flutter.h>

/// iOS counterpart to Android's `BotRuntime` method channel.
///
/// It deliberately calls the bundled Python `agent.mobile_gateway` module, so
/// the trading, risk, scoring, and execution code remains identical to Android.
@interface MaxAlphaPythonBridge : NSObject
+ (void)installWithMessenger:(NSObject<FlutterBinaryMessenger> *)messenger;
@end
