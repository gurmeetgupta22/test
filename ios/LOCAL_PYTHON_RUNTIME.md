# Local Python runtime for iOS

The iOS bridge is implemented in `Runner/MaxAlphaPythonBridge.mm`. It calls the
same `agent.mobile_gateway` functions as Android: `configure`, `dashboard`,
`logs`, `signals`, `start_bot`, and `stop_bot`. Do not port or duplicate bot
logic in Swift: the Python `agent` source is staged unchanged by
`scripts/stage_python_agent.sh` during every Xcode build.

## One-time macOS setup

1. Build or obtain an iOS CPython `Python.xcframework` that exports
   `<Python/Python.h>` and supports `arm64` devices and `arm64` simulator.
2. Include iOS-compatible builds of the Python dependencies used by `agent`
   (`requests`, `python-dotenv`, `pytz`, `numpy`, `pandas`, `beautifulsoup4`,
   `dhanhq`, and their dependencies) and its Python standard library.
3. Place it at `ios/Frameworks/Python.xcframework`.
4. Copy `ios/Flutter/PythonRuntime.xcconfig.example` to
   `ios/Flutter/PythonRuntime.xcconfig` and run `flutter build ios` or build
   `Runner.xcworkspace` in Xcode.

The native bridge copies the bundled source to
`Application Support/MaxAlpha/python`, sets that as the process working
directory, and then imports `agent.mobile_gateway`. This preserves the existing
on-device files and all trading logic.

iOS may suspend the process when the app backgrounds; the local engine is
therefore foreground-only. This is an iOS platform constraint, not a change to
the trading algorithm.
