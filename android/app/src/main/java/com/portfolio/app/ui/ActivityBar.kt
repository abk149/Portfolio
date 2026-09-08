package com.portfolio.app.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * A slim, always-present strip showing every analysis currently running.
 *
 * This is the replacement for blocking progress dialogs. Work is submitted and
 * then simply runs; this bar is how you know it still is. It sits between the
 * top bar and the content on every screen, so you can start a deep dive, walk
 * over to the portfolio tab, and still see it working.
 *
 * Tapping expands to a per-task list with progress where the backend reports it.
 */
@Composable
fun ActivityBar() {
    val jobs = JobBus.active()
    var expanded by remember { mutableStateOf(false) }

    // Collapse automatically once everything finishes, so it doesn't linger.
    LaunchedEffect(jobs.isEmpty()) { if (jobs.isEmpty()) expanded = false }

    AnimatedVisibility(visible = jobs.isNotEmpty()) {
        Column(
            Modifier
                .fillMaxWidth()
                .background(Panel2)
                .clickable { expanded = !expanded }
                .padding(horizontal = 14.dp, vertical = 8.dp)
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(
                    Modifier.size(13.dp), strokeWidth = 2.dp, color = AccentHi)
                Spacer(Modifier.width(10.dp))
                Text(
                    if (jobs.size == 1) jobs[0].second.label
                    else "${jobs.size} tasks running",
                    color = OnBg, fontSize = 12.sp, fontWeight = FontWeight.Medium,
                    modifier = Modifier.weight(1f), maxLines = 1,
                )
                Text(if (expanded) "▲" else "▼", color = Muted, fontSize = 10.sp)
            }
            if (!expanded && jobs.size == 1) {
                jobs[0].second.detail?.let {
                    Spacer(Modifier.height(3.dp))
                    Text(it, color = Muted, fontSize = 10.sp, maxLines = 1)
                }
            }
            if (expanded) {
                Spacer(Modifier.height(4.dp))
                Text("Running in the background — you can keep using the app, and " +
                    "these continue if you minimise it.",
                    color = Muted, fontSize = 10.sp, lineHeight = 14.sp)
                Spacer(Modifier.height(8.dp))
                jobs.forEach { (_, st) ->
                    Column(Modifier.fillMaxWidth().padding(vertical = 5.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text("•", color = AccentHi, fontSize = 11.sp)
                            Spacer(Modifier.width(8.dp))
                            Text(st.label, color = OnBg, fontSize = 11.5.sp,
                                modifier = Modifier.weight(1f), maxLines = 1)
                            st.progress?.let {
                                Text("%.0f%%".format(it * 100), color = AccentHi, fontSize = 11.sp)
                            }
                        }
                        st.progress?.let {
                            Spacer(Modifier.height(4.dp))
                            LinearProgressIndicator(
                                progress = it.coerceIn(0f, 1f),
                                modifier = Modifier.fillMaxWidth().height(3.dp),
                                color = AccentHi, trackColor = Panel,
                            )
                        }
                        (st.detail ?: st.status)?.let {
                            Spacer(Modifier.height(3.dp))
                            Text(it, color = Muted, fontSize = 10.sp, maxLines = 2,
                                lineHeight = 14.sp)
                        }
                    }
                }
            }
        }
    }
}
