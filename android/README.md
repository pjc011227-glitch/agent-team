# Agent Team Android APK · Preview 0.1

This is a **native Android WebView client**, not a standalone Android AI runtime. It shares the existing Web/PWA chat and task interface with desktop users. It needs a reachable, trusted **HTTPS** Agent Team Hub running version >=0.4.0; remote desktop tasks also require a separately installed Windows/Linux Worker. Use Android 8.0+ (API 26+).

## Build

Use GitHub Actions > **Android APK** > Run workflow. The workflow builds `android/app/build/outputs/apk/debug/app-debug.apk`, uploads the APK as an Actions artifact and publishes a public prerelease tagged `android-preview-v0.1` (debug signing, test only). You can also build with a local Android SDK: `cd android && gradle :app:assembleDebug` (Java 17, Gradle 8.9, Android SDK 35).

## How to use

1. Deploy the [Hub](../docs/DEPLOYMENT.md) behind HTTPS, set `AGENT_TEAM_ACCESS_TOKEN`, and start a desktop worker if remote control is required.
2. Install the APK on Android. Enter the Hub address, e.g. `https://your-domain.example` (no URL path).
3. Open **聊天** to use agents and groups, or **电脑任务** to pair/operate the Windows/Linux worker.
4. The owner access token is requested by the Hub web UI. It is stored only in the WebView session storage until the session ends.

## Limitations and security

- APK requires your own running Hub; does not contain or run LLMs. This first build is **Android wrapper, not fully-native app**.
- APK is an openly downloadable prerelease signed with the ephemeral Android debug key generated on CI. Do **not** trust it for production use or expect seamless upgrades across separate CI runs.
- HTTPS is mandatory, cleartext HTTP and certificate-error bypass are disabled. App does not include any API key or device token.
- ChatGPT Plus and similar consumer subscriptions are not automatically usable as third-party API quotas.
- Remote control limited to allowlisted Worker tasks implemented by the server; no arbitrary shell or GUI control.
- No push notifications, background sync, offline Hub, biometric lock, or remote deployment automation yet.
