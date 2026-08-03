plugins {
    id("com.android.application")
    id("kotlin-android")
    id("com.chaquo.python")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

android {
    namespace = "com.example.max_alpha_mobile"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = JavaVersion.VERSION_17.toString()
    }

    defaultConfig {
        // TODO: Specify your own unique Application ID (https://developer.android.com/studio/build/application-id.html).
        applicationId = "com.example.max_alpha_mobile"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = 24
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    buildTypes {
        release {
            // TODO: Add your own signing config for the release build.
            // Signing with the debug keys for now, so `flutter run --release` works.
            signingConfig = signingConfigs.getByName("debug")
        }
    }
}

val stagedPythonDir = layout.buildDirectory.dir("generated/mobile-python")
val stageMobilePython by tasks.registering(Copy::class) {
    from(file("../agent")) {
        exclude("__pycache__/**", "**/*.pyc", "mobile_api.py",
                "main_ios.py", "mobile_gateway_http.py", "requirements_ios.txt")
        into("agent")
    }
    from(file("../agent/symbol_map.json")) { into("agent") }
    from(file("../assets/dashboard_v5_bot2.html")) { into("agent") }
    into(stagedPythonDir)
}

tasks.matching { it.name.startsWith("merge") && it.name.endsWith("PythonSources") }
    .configureEach { dependsOn(stageMobilePython) }

chaquopy {
    defaultConfig {
        // Chaquopy installs the Python dependencies during the Android build.
        // Select the Android runtime version first, then use the matching
        // local interpreter to build dependency metadata.
        version = "3.11"
        // Use CHAQUOPY_PYTHON env var for CI (Codemagic macOS: python3).
        // Falls back to the local Windows path for dev machines.
        val pyExe = System.getenv("CHAQUOPY_PYTHON")
            ?: if (org.gradle.internal.os.OperatingSystem.current().isWindows)
                "C:/Users/hanu2/AppData/Local/Programs/Python/Python311/python.exe"
               else
                "/usr/bin/python3"
        buildPython(pyExe)
        pip {
            install("requests>=2.31.0")
            install("python-dotenv>=1.0.1")
            install("pytz>=2024.1")
            install("numpy>=1.26.0")
            install("pandas>=2.0.0")
            install("beautifulsoup4>=4.12.0")
            install("dhanhq>=2.0.2")
            install("yfinance>=0.2.54")
        }
    }
    sourceSets {
        getByName("main") { srcDir(stagedPythonDir) }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
}

flutter {
    source = "../.."
}
