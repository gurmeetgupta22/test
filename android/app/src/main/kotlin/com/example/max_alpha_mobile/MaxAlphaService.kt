package com.example.max_alpha_mobile

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat

class MaxAlphaService : Service() {
    companion object {
        const val EXTRA_BUDGET = "budget"
        private const val CHANNEL = "max_alpha_running"
        private const val NOTIFICATION_ID = 4102
        @Volatile var isRunning = false
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        createChannel()
        val notification = NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(com.example.max_alpha_mobile.R.mipmap.ic_launcher)
            .setContentTitle("Max Alpha is running")
            .setContentText("Local Python trading engine is active on this device.")
            .setOngoing(true)
            .build()
        startForeground(NOTIFICATION_ID, notification)
        isRunning = true
        val budget = intent?.getDoubleExtra(EXTRA_BUDGET, -1.0) ?: -1.0
        Thread {
            try {
                val value: Any? = if (budget > 0) budget else null
                BotRuntime.gateway(this).callAttr("start_bot", value)
            } catch (error: Exception) {
                try {
                    BotRuntime.gateway(this).callAttr("record_error", "Android service startup error: ${error.message}")
                } catch (_: Exception) { }
                stopSelf()
            }
        }.start()
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        try { BotRuntime.gateway(this).callAttr("stop_bot") } catch (_: Exception) { }
        isRunning = false
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun createChannel() {
        val manager = ContextCompat.getSystemService(this, NotificationManager::class.java) ?: return
        manager.createNotificationChannel(NotificationChannel(CHANNEL, "Max Alpha bot", NotificationManager.IMPORTANCE_LOW))
    }
}
