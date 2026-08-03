package com.example.max_alpha_mobile

import android.content.Context
import android.content.Intent
import android.os.Build
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.embedding.android.FlutterActivity
import io.flutter.plugin.common.MethodChannel
import org.json.JSONArray
import org.json.JSONObject

class MainActivity : FlutterActivity() {
    private val channelName = "com.maxalpha.mobile/bot"

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, channelName)
            .setMethodCallHandler { call, result ->
                try {
                    when (call.method) {
                        "configure" -> {
                            val values = call.arguments as? Map<String, Any?> ?: emptyMap()
                            BotRuntime.gateway(this).callAttr("configure", values)
                            result.success(mapOf("configured" to true))
                        }
                        "dashboard" -> result.success(BotRuntime.map(this, "dashboard"))
                        "logs" -> result.success(mapOf(
                            "lines" to BotRuntime.gateway(this).callAttr("logs").asList().map { it.toString() },
                            "running" to BotRuntime.gateway(this).callAttr("is_running").toBoolean(),
                        ))
                        "signals" -> result.success(mapOf(
                            "content" to BotRuntime.gateway(this).callAttr("signals").toString(),
                        ))
                        "startDashboard" -> {
                            // Return the two scalar values explicitly. This avoids any
                            // method-codec ambiguity around a nested Python dict.
                            val server = BotRuntime.gateway(this).callAttr("start_dashboard")
                            result.success(mapOf(
                                "running" to server.callAttr("get", "running").toBoolean(),
                                "url" to server.callAttr("get", "url").toString(),
                            ))
                        }
                        "startBot" -> {
                            val values = call.arguments as? Map<String, Any?> ?: emptyMap()
                            val config = values["configuration"] as? Map<String, Any?> ?: emptyMap()
                            BotRuntime.gateway(this).callAttr("configure", config)
                            val budget = (values["budget"] as? Number)?.toDouble() ?: -1.0
                            val intent = Intent(this, MaxAlphaService::class.java)
                                .putExtra(MaxAlphaService.EXTRA_BUDGET, budget)
                            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) startForegroundService(intent)
                            else startService(intent)
                            result.success(null)
                        }
                        "stopBot" -> {
                            stopService(Intent(this, MaxAlphaService::class.java))
                            result.success(null)
                        }
                        else -> result.notImplemented()
                    }
                } catch (error: Exception) {
                    result.error("BOT_ERROR", error.message ?: "Python bridge failed", null)
                }
            }
    }
}

object BotRuntime {
    fun gateway(context: Context): com.chaquo.python.PyObject {
        if (!Python.isStarted()) Python.start(AndroidPlatform(context.applicationContext))
        return Python.getInstance().getModule("agent.mobile_gateway")
    }

    fun map(context: Context, method: String): Map<String, Any?> {
        // Chaquopy cannot reliably coerce a Python dict (especially one with
        // nested portfolio structures) directly into java.util.Map. JSON keeps
        // the Python gateway's data intact before Flutter receives it.
        val value = gateway(context).callAttr(method)
        val encoded = Python.getInstance().getModule("json").callAttr("dumps", value).toString()
        return JSONObject(encoded).toFlutterMap()
    }
}

private fun JSONObject.toFlutterMap(): Map<String, Any?> = buildMap {
    val keys = keys()
    while (keys.hasNext()) {
        val key = keys.next()
        put(key, get(key).toFlutterValue())
    }
}

private fun JSONArray.toFlutterList(): List<Any?> = List(length()) { index ->
    get(index).toFlutterValue()
}

private fun Any?.toFlutterValue(): Any? = when (this) {
    JSONObject.NULL -> null
    is JSONObject -> toFlutterMap()
    is JSONArray -> toFlutterList()
    else -> this
}
