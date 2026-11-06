package com.agentteam.mobile;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.JsPromptResult;
import android.webkit.JsResult;
import android.webkit.SslErrorHandler;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

/**
 * Native Android shell for an existing self-hosted Agent Team Hub.
 * The AI agents and desktop worker run on the Hub/desktop, never in the APK.
 */
public final class MainActivity extends Activity {
    private static final String PREFS = "agent_team_mobile";
    private static final String PREF_URL = "hub_url";
    private static final int NAVY = Color.rgb(16, 20, 25);
    private static final int LIGHT = Color.rgb(239, 245, 249);
    private LinearLayout frame;
    private WebView web;
    private String baseUrl;
    private String currentPath = "/";

    private TextView text(String content, int sp) {
        TextView t = new TextView(this);
        t.setText(content);
        t.setTextSize(sp);
        t.setTextColor(LIGHT);
        t.setPadding(0, dp(7), 0, dp(7));
        return t;
    }
    private int dp(int n) { return Math.round(n * getResources().getDisplayMetrics().density); }
    private LinearLayout vertical() {
        LinearLayout l = new LinearLayout(this);
        l.setOrientation(LinearLayout.VERTICAL);
        l.setBackgroundColor(NAVY);
        l.setPadding(dp(18), dp(20), dp(18), dp(20));
        return l;
    }
    private Button button(String title, View.OnClickListener click) {
        Button b = new Button(this);
        b.setText(title);
        b.setAllCaps(false);
        b.setOnClickListener(click);
        return b;
    }
    private void show(View view) {
        frame.removeAllViews();
        frame.addView(view, new LinearLayout.LayoutParams(-1, -1));
    }

    @Override public void onCreate(Bundle savedState) {
        super.onCreate(savedState);
        getWindow().setStatusBarColor(NAVY);
        getWindow().setNavigationBarColor(NAVY);
        frame = new LinearLayout(this);
        frame.setOrientation(LinearLayout.VERTICAL);
        frame.setBackgroundColor(NAVY);
        setContentView(frame);
        baseUrl = getPreferences(MODE_PRIVATE).getString(PREF_URL, "");
        if (baseUrl.isEmpty()) showSetup(null);
        else showBrowser("/");
    }

    private String validateUrl(String input) {
        String s = input.trim();
        Uri uri = Uri.parse(s);
        // HTTPS only. Never silently send account/API credentials over plain HTTP.
        if (!"https".equalsIgnoreCase(uri.getScheme()) || uri.getHost() == null ||
            uri.getHost().isEmpty() || uri.getUserInfo() != null ||
            (uri.getPath() != null && !uri.getPath().equals("/") && !uri.getPath().isEmpty()) ||
            uri.getQuery() != null || uri.getFragment() != null) {
            throw new IllegalArgumentException("必须输入 HTTPS 服务器地址，例如 https://agent.example.com（不能附带路径）");
        }
        String host = uri.getHost().toLowerCase(java.util.Locale.ROOT);
        return "https://" + host + (uri.getPort() == -1 ? "" : ":" + uri.getPort());
    }

    private void showSetup(String error) {
        if (web != null) { web.stopLoading(); web.destroy(); web = null; }
        ScrollView scroll = new ScrollView(this);
        LinearLayout content = vertical();
        content.setMinimumHeight(getResources().getDisplayMetrics().heightPixels);
        TextView title = text("✦  智能体团队", 27);
        title.setTypeface(null, Typeface.BOLD);
        content.addView(title);
        content.addView(text("Android 客户端预览版 · 连接你自己的 Agent Team 服务器", 15));
        content.addView(text("需要先部署 Hub 并配置 HTTPS，然后在下方填写域名。APK 不会自行运行 AI，也不能脱离电脑端 Worker 操作电脑。", 14));
        content.addView(text("服务器地址（HTTPS）", 15));
        EditText endpoint = new EditText(this);
        endpoint.setTextColor(LIGHT);
        endpoint.setHintTextColor(Color.LTGRAY);
        endpoint.setSingleLine(true);
        endpoint.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        endpoint.setHint("https://agent.example.com");
        endpoint.setText(baseUrl);
        content.addView(endpoint, new LinearLayout.LayoutParams(-1, dp(52)));
        TextView feedback = text(error == null ? "" : error, 14);
        feedback.setTextColor(Color.rgb(255, 168, 168));
        content.addView(feedback);
        content.addView(button("连接服务器", v -> {
            try {
                String next = validateUrl(endpoint.getText().toString());
                if (!next.equals(baseUrl)) {
                    // Discard the old server's ephemeral webview session when changing origins.
                    if (web != null) { web.clearHistory(); web.clearCache(true); }
                }
                baseUrl = next;
                getPreferences(MODE_PRIVATE).edit().putString(PREF_URL, baseUrl).apply();
                showBrowser("/");
            } catch (IllegalArgumentException ex) {
                feedback.setText(ex.getMessage());
            }
        }));
        content.addView(text("安全提醒：只连接你信任的服务器；服务器需要配置 AGENT_TEAM_ACCESS_TOKEN，远程控制必须明确授权。", 13));
        scroll.addView(content);
        show(scroll);
    }

    private Button nav(String name, String route) {
        Button b = button(name, v -> navigate(route));
        b.setTextSize(12);
        b.setTextColor(LIGHT);
        b.setBackgroundTintList(android.content.res.ColorStateList.valueOf(Color.rgb(43, 61, 77)));
        return b;
    }

    private void showBrowser(String path) {
        if (baseUrl == null || baseUrl.isEmpty()) { showSetup(null); return; }
        LinearLayout page = new LinearLayout(this);
        page.setBackgroundColor(NAVY);
        page.setOrientation(LinearLayout.VERTICAL);
        LinearLayout navbar = new LinearLayout(this);
        navbar.setOrientation(LinearLayout.HORIZONTAL);
        navbar.setGravity(Gravity.CENTER_VERTICAL);
        navbar.setPadding(dp(6), dp(4), dp(6), dp(4));
        navbar.addView(nav("聊天", "/"), new LinearLayout.LayoutParams(0, dp(48), 1));
        navbar.addView(nav("电脑任务", "/remote.html"), new LinearLayout.LayoutParams(0, dp(48), 1));
        navbar.addView(button("⚙", v -> new AlertDialog.Builder(this)
            .setTitle("服务器设置")
            .setMessage("已连接：" + baseUrl + "\n更换服务器将清除当前网页会话。")
            .setNegativeButton("取消", null)
            .setPositiveButton("更换", (d, which) -> showSetup(null))
            .show()), new LinearLayout.LayoutParams(dp(52), dp(48)));
        page.addView(navbar);
        web = new WebView(this);
        web.setBackgroundColor(NAVY);
        web.getSettings().setJavaScriptEnabled(true);
        web.getSettings().setDomStorageEnabled(true);
        web.getSettings().setAllowFileAccess(false);
        web.getSettings().setAllowContentAccess(false);
        web.getSettings().setJavaScriptCanOpenWindowsAutomatically(false);
        web.getSettings().setMixedContentMode(android.webkit.WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        web.getSettings().setBuiltInZoomControls(false);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false);
        web.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onJsPrompt(WebView view, String url, String message,
                String defaultValue, JsPromptResult result) {
                EditText input = new EditText(MainActivity.this);
                input.setSingleLine(true);
                input.setText(defaultValue);
                input.setPadding(dp(18), dp(10), dp(18), dp(10));
                if (message.contains("口令") || message.contains("密钥")) {
                    input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
                }
                new AlertDialog.Builder(MainActivity.this).setTitle(message)
                    .setView(input).setPositiveButton("确定", (d, which) -> result.confirm(input.getText().toString()))
                    .setNegativeButton("取消", (d, which) -> result.cancel())
                    .setOnCancelListener(d -> result.cancel()).show();
                return true;
            }
            @Override public boolean onJsConfirm(WebView view, String url, String message, JsResult result) {
                new AlertDialog.Builder(MainActivity.this).setMessage(message)
                    .setPositiveButton("确定", (d, which) -> result.confirm())
                    .setNegativeButton("取消", (d, which) -> result.cancel())
                    .setOnCancelListener(d -> result.cancel()).show();
                return true;
            }
            @Override public boolean onJsAlert(WebView view, String url, String message, JsResult result) {
                new AlertDialog.Builder(MainActivity.this).setMessage(message)
                    .setPositiveButton("确定", (d, which) -> result.confirm())
                    .setOnCancelListener(d -> result.confirm()).show();
                return true;
            }
        });
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri target = request.getUrl();
                Uri trusted = Uri.parse(baseUrl);
                if ("https".equalsIgnoreCase(target.getScheme()) &&
                    trusted.getAuthority().equalsIgnoreCase(target.getAuthority())) return false;
                try { startActivity(new Intent(Intent.ACTION_VIEW, target)); }
                catch (ActivityNotFoundException ignored) { }
                return true;
            }
            @Override public void onReceivedSslError(WebView view, SslErrorHandler handler, android.net.http.SslError error) {
                handler.cancel();
                new AlertDialog.Builder(MainActivity.this).setTitle("HTTPS 证书错误")
                    .setMessage("服务器证书无效，已阻止连接。请先修复服务器证书。")
                    .setPositiveButton("返回设置", (d, w) -> showSetup("HTTPS 证书校验失败"))
                    .show();
            }
            @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    new AlertDialog.Builder(MainActivity.this).setTitle("连接失败")
                        .setMessage("无法打开服务器，请确认服务器已启动，且手机可以访问 HTTPS 域名。")
                        .setNegativeButton("重试", (d, w) -> web.reload())
                        .setPositiveButton("设置地址", (d, w) -> showSetup(null)).show();
                }
            }
        });
        page.addView(web, new LinearLayout.LayoutParams(-1, 0, 1));
        show(page);
        navigate(path);
    }

    private void navigate(String path) {
        currentPath = path;
        if (web != null) web.loadUrl(baseUrl + path);
    }

    @Override public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack();
        else if (web != null) new AlertDialog.Builder(this).setTitle("退出")
            .setMessage("退出智能体团队？")
            .setNegativeButton("继续使用", null)
            .setPositiveButton("退出", (d, w) -> finish()).show();
        else super.onBackPressed();
    }
    @Override protected void onDestroy() {
        if (web != null) { web.stopLoading(); web.destroy(); web = null; }
        super.onDestroy();
    }
}
