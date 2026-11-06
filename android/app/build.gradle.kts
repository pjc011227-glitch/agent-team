plugins {
    id("com.android.application")
}

android {
    namespace = "com.agentteam.mobile"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.agentteam.mobile"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0-preview"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
