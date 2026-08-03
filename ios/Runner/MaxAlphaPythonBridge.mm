#import "MaxAlphaPythonBridge.h"

#import <Foundation/Foundation.h>
#import <stdlib.h>
#import <unistd.h>

// Define MAX_ALPHA_PYTHON in ios/Flutter/PythonRuntime.xcconfig after placing
// a CPython XCFramework at ios/Frameworks/Python.xcframework. Keeping this
// source guarded lets the ordinary Flutter UI build before that Mac-only
// runtime is installed, without silently substituting different bot logic.
#if MAX_ALPHA_PYTHON
#import <Python/Python.h>
#endif

static NSString *const MaxAlphaChannel = @"com.maxalpha.mobile/bot";

@interface MaxAlphaPythonBridge ()
@property(nonatomic) dispatch_queue_t queue;
#if MAX_ALPHA_PYTHON
@property(nonatomic) BOOL initialized;
@property(nonatomic, copy) NSString *runtimeDirectory;
#endif
@end

@implementation MaxAlphaPythonBridge

+ (instancetype)sharedBridge {
  static MaxAlphaPythonBridge *bridge;
  static dispatch_once_t onceToken;
  dispatch_once(&onceToken, ^{
    bridge = [MaxAlphaPythonBridge new];
    bridge.queue = dispatch_queue_create("com.maxalpha.mobile.python", DISPATCH_QUEUE_SERIAL);
  });
  return bridge;
}

+ (void)installWithMessenger:(NSObject<FlutterBinaryMessenger> *)messenger {
  FlutterMethodChannel *channel = [FlutterMethodChannel methodChannelWithName:MaxAlphaChannel
                                                               binaryMessenger:messenger];
  [channel setMethodCallHandler:^(FlutterMethodCall *call, FlutterResult result) {
    [[self sharedBridge] handle:call result:result];
  }];
}

- (void)handle:(FlutterMethodCall *)call result:(FlutterResult)result {
  dispatch_async(self.queue, ^{
#if MAX_ALPHA_PYTHON
    NSError *error = nil;
    id value = [self invoke:call.method arguments:call.arguments error:&error];
    dispatch_async(dispatch_get_main_queue(), ^{
      if (error) {
        result([FlutterError errorWithCode:@"BOT_ERROR" message:error.localizedDescription details:nil]);
      } else if (value == FlutterMethodNotImplemented) {
        result(FlutterMethodNotImplemented);
      } else {
        result(value);
      }
    });
#else
    dispatch_async(dispatch_get_main_queue(), ^{
      result([FlutterError errorWithCode:@"PYTHON_RUNTIME_MISSING"
                                  message:@"The iOS Python runtime has not been installed. See ios/LOCAL_PYTHON_RUNTIME.md."
                                  details:nil]);
    });
#endif
  });
}

#if MAX_ALPHA_PYTHON

- (id)invoke:(NSString *)method arguments:(id)arguments error:(NSError **)error {
  if (![self prepare:error]) return nil;

  if ([method isEqualToString:@"configure"]) {
    return [self callGateway:@"configure" jsonArgument:arguments error:error];
  }
  if ([method isEqualToString:@"dashboard"]) {
    return [self callGateway:@"dashboard" jsonArgument:nil error:error];
  }
  if ([method isEqualToString:@"signals"]) {
    NSString *content = [self callGateway:@"signals" jsonArgument:nil error:error];
    return content ? @{ @"content": content } : nil;
  }
  if ([method isEqualToString:@"startDashboard"]) {
    return [self callGateway:@"start_dashboard" jsonArgument:nil error:error];
  }
  if ([method isEqualToString:@"logs"]) {
    NSArray *lines = [self callGateway:@"logs" jsonArgument:nil error:error];
    if (!lines) return nil;
    NSNumber *running = [self callGateway:@"is_running" jsonArgument:nil error:error];
    return running ? @{ @"lines": lines, @"running": running } : nil;
  }
  if ([method isEqualToString:@"startBot"]) {
    NSDictionary *values = [arguments isKindOfClass:NSDictionary.class] ? arguments : @{};
    NSDictionary *configuration = [values[@"configuration"] isKindOfClass:NSDictionary.class]
        ? values[@"configuration"] : @{};
    if (![self callGateway:@"configure" jsonArgument:configuration error:error]) return nil;
    NSNumber *budget = [values[@"budget"] isKindOfClass:NSNumber.class] ? values[@"budget"] : nil;
    return [self callStart:budget error:error] ? nil : nil;
  }
  if ([method isEqualToString:@"stopBot"]) {
    [self callGateway:@"stop_bot" jsonArgument:nil error:error];
    return nil;
  }
  return FlutterMethodNotImplemented;
}

- (BOOL)prepare:(NSError **)error {
  if (self.initialized) return YES;
  NSString *bundleRuntime = [[NSBundle mainBundle] pathForResource:@"python" ofType:nil];
  if (!bundleRuntime) {
    *error = [NSError errorWithDomain:@"MaxAlpha" code:1 userInfo:@{NSLocalizedDescriptionKey: @"Bundled agent source is missing."}];
    return NO;
  }
  NSURL *support = [[NSFileManager defaultManager] URLsForDirectory:NSApplicationSupportDirectory
                                                           inDomains:NSUserDomainMask].firstObject;
  NSURL *runtime = [support URLByAppendingPathComponent:@"MaxAlpha" isDirectory:YES];
  NSFileManager *files = NSFileManager.defaultManager;
  if (![files fileExistsAtPath:runtime.path]) {
    if (![files createDirectoryAtURL:runtime withIntermediateDirectories:YES attributes:nil error:error]) return NO;
    if (![files copyItemAtPath:bundleRuntime toPath:[runtime.path stringByAppendingPathComponent:@"python"] error:error]) return NO;
  }
  self.runtimeDirectory = runtime.path;
  setenv("PYTHONNOUSERSITE", "1", 1);
  setenv("PYTHONDONTWRITEBYTECODE", "1", 1);
  chdir(self.runtimeDirectory.fileSystemRepresentation);
  Py_Initialize();
  if (!Py_IsInitialized()) {
    *error = [NSError errorWithDomain:@"MaxAlpha" code:2 userInfo:@{NSLocalizedDescriptionKey: @"CPython failed to initialize."}];
    return NO;
  }
  NSString *pythonPath = [self.runtimeDirectory stringByAppendingPathComponent:@"python"];
  NSString *bootstrap = [NSString stringWithFormat:@"import os, sys\\n"
                       "runtime = %@\\n"
                       "if runtime not in sys.path: sys.path.insert(0, runtime)\\n"
                       "os.chdir(%@)\\n"
                       "import agent.mobile_gateway\\n", [self pythonLiteral:pythonPath], [self pythonLiteral:self.runtimeDirectory]];
  PyGILState_STATE gil = PyGILState_Ensure();
  int bootstrapResult = PyRun_SimpleString(bootstrap.UTF8String);
  if (bootstrapResult != 0) {
    *error = [NSError errorWithDomain:@"MaxAlpha" code:3 userInfo:@{NSLocalizedDescriptionKey: [self pythonError]}];
    PyGILState_Release(gil);
    return NO;
  }
  PyGILState_Release(gil);
  self.initialized = YES;
  return YES;
}

- (id)callGateway:(NSString *)method jsonArgument:(id)argument error:(NSError **)error {
  NSString *expression;
  if (argument) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:argument options:0 error:error];
    if (!data) return nil;
    NSString *encoded = [data base64EncodedStringWithOptions:0];
    expression = [NSString stringWithFormat:@"agent.mobile_gateway.%@(__import__('json').loads(__import__('base64').b64decode('%@').decode('utf-8')))", method, encoded];
  } else {
    expression = [NSString stringWithFormat:@"agent.mobile_gateway.%@()", method];
  }
  return [self evaluateJSONExpression:expression error:error];
}

- (BOOL)callStart:(NSNumber *)budget error:(NSError **)error {
  NSString *argument = budget ? budget.stringValue : @"None";
  id ignored = [self evaluateJSONExpression:[NSString stringWithFormat:@"agent.mobile_gateway.start_bot(%@)", argument] error:error];
  return !*error && ignored != nil;
}

- (id)evaluateJSONExpression:(NSString *)expression error:(NSError **)error {
  PyGILState_STATE gil = PyGILState_Ensure();
  NSString *source = [NSString stringWithFormat:@"import json, agent.mobile_gateway\\njson.dumps((%@), default=str)", expression];
  PyObject *main = PyImport_AddModule("__main__");
  PyObject *globals = main ? PyModule_GetDict(main) : NULL;
  PyObject *value = globals ? PyRun_StringFlags(source.UTF8String, Py_eval_input, globals, globals, NULL) : NULL;
  if (!value) {
    *error = [NSError errorWithDomain:@"MaxAlpha" code:4 userInfo:@{NSLocalizedDescriptionKey: [self pythonError]}];
    PyGILState_Release(gil);
    return nil;
  }
  const char *utf8 = PyUnicode_AsUTF8(value);
  NSString *json = utf8 ? [NSString stringWithUTF8String:utf8] : nil;
  Py_DECREF(value);
  PyGILState_Release(gil);
  if (!json) {
    *error = [NSError errorWithDomain:@"MaxAlpha" code:5 userInfo:@{NSLocalizedDescriptionKey: @"Python returned non-text JSON."}];
    return nil;
  }
  NSData *data = [json dataUsingEncoding:NSUTF8StringEncoding];
  return [NSJSONSerialization JSONObjectWithData:data options:0 error:error];
}

- (NSString *)pythonLiteral:(NSString *)value {
  NSString *escaped = [[value stringByReplacingOccurrencesOfString:@"\\" withString:@"\\\\"] stringByReplacingOccurrencesOfString:@"'" withString:@"\\'"];
  return [NSString stringWithFormat:@"'%@'", escaped];
}

- (NSString *)pythonError {
  PyObject *type = NULL, *value = NULL, *traceback = NULL;
  PyErr_Fetch(&type, &value, &traceback);
  PyErr_NormalizeException(&type, &value, &traceback);
  PyObject *text = value ? PyObject_Str(value) : NULL;
  const char *message = text ? PyUnicode_AsUTF8(text) : NULL;
  NSString *result = message ? [NSString stringWithUTF8String:message] : @"Unknown Python error";
  Py_XDECREF(text); Py_XDECREF(type); Py_XDECREF(value); Py_XDECREF(traceback);
  return result;
}

#endif
@end
