group = "com.flet.serious_python_android"
version = "4.5.1"

apply(plugin = "com.android.library")

configure<com.android.build.gradle.LibraryExtension> {
    namespace = "com.flet.serious_python_android"
    compileSdk = 34
    defaultConfig {
        minSdk = 21
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
