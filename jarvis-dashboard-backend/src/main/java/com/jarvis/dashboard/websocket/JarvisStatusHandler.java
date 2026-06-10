package com.jarvis.dashboard.websocket;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.*;
import org.springframework.web.socket.handler.TextWebSocketHandler;

import java.io.IOException;
import java.time.Instant;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 원시 WebSocket 핸들러 — Electron HUD 실시간 상태 동기화.
 *
 * 브로드캐스트 이벤트 타입:
 *   status        → JARVIS 전체 상태 (IDLE / LISTENING / CHATTING / WORKING / ERROR)
 *   engine_change → AI 엔진 전환
 *   os_action     → OS 에이전트 실행 로그
 *   ngrok_ready   → ngrok 터널 활성화 알림
 */
@Component
@Slf4j
public class JarvisStatusHandler extends TextWebSocketHandler {

    private final Set<WebSocketSession> sessions = ConcurrentHashMap.newKeySet();
    private final ObjectMapper objectMapper = new ObjectMapper();

    // 현재 JARVIS 상태 (캐시 — 신규 접속 클라이언트에 즉시 전송용)
    private volatile String currentState  = "IDLE";
    private volatile String currentEngine = "OLLAMA_DEEPSEEK";
    private volatile String currentAgent  = "life_coach";

    // ── 세션 관리 ─────────────────────────────────────────────────────────────

    @Override
    public void afterConnectionEstablished(WebSocketSession session) {
        sessions.add(session);
        log.info("[WS] 새 클라이언트 연결: {} (총 {}개)", session.getId(), sessions.size());

        // 신규 접속 → 현재 상태 즉시 전송
        sendTo(session, buildStatusPayload(currentState, currentEngine, currentAgent));
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
        sessions.remove(session);
        log.info("[WS] 클라이언트 연결 해제: {} (남은 {}개)", session.getId(), sessions.size());
    }

    @Override
    public void handleTransportError(WebSocketSession session, Throwable ex) {
        log.warn("[WS] 전송 오류 ({}): {}", session.getId(), ex.getMessage());
        sessions.remove(session);
    }

    // ── 수신 처리 ─────────────────────────────────────────────────────────────

    @Override
    protected void handleTextMessage(WebSocketSession session, TextMessage message) {
        log.debug("[WS] 수신: {}", message.getPayload());
        try {
            JsonNode json = objectMapper.readTree(message.getPayload());
            JsonNode eventNode = json.get("event");
            if (eventNode != null && "wake_up".equals(eventNode.asText())) {
                log.info("[WS] 박수 WAKE_UP 수신 — 전체 클라이언트에 중계");
                currentState = "LISTENING";
                broadcast(Map.of(
                    "event", "wake_up",
                    "state", "LISTENING",
                    "ts",    Instant.now().toEpochMilli()
                ));
            }
        } catch (Exception ignored) {}
    }

    // ── 브로드캐스트 Public API ───────────────────────────────────────────────

    /** JARVIS 상태 전환 브로드캐스트 */
    public void broadcastStatus(String state, String engineKey, String agentKey) {
        currentState  = state;
        currentEngine = engineKey;
        currentAgent  = agentKey;
        broadcast(buildStatusPayload(state, engineKey, agentKey));
    }

    /** AI 엔진 변경 브로드캐스트 */
    public void broadcastEngineChange(String engineKey, String engineName, String tier) {
        currentEngine = engineKey;
        broadcast(Map.of(
            "event",       "engine_change",
            "engine_key",  engineKey,
            "engine_name", engineName,
            "tier",        tier,
            "ts",          Instant.now().toEpochMilli()
        ));
    }

    /** OS 에이전트 실행 로그 브로드캐스트 */
    public void broadcastOsAction(String ndjsonLine) {
        broadcast(Map.of(
            "event",   "os_action",
            "payload", ndjsonLine,
            "ts",      Instant.now().toEpochMilli()
        ));
    }

    /** ngrok 터널 활성화 알림 */
    public void broadcastNgrokReady(String domain) {
        broadcast(Map.of(
            "event",  "ngrok_ready",
            "domain", domain,
            "ts",     Instant.now().toEpochMilli()
        ));
    }

    public int getConnectedCount() {
        return sessions.size();
    }

    // ── 내부 유틸 ─────────────────────────────────────────────────────────────

    private Map<String, Object> buildStatusPayload(String state, String engine, String agent) {
        return Map.of(
            "event",      "status",
            "state",      state,
            "engine_key", engine,
            "agent_key",  agent,
            "ts",         Instant.now().toEpochMilli()
        );
    }

    private void broadcast(Object payload) {
        String json;
        try {
            json = objectMapper.writeValueAsString(payload);
        } catch (Exception e) {
            log.error("[WS] JSON 직렬화 실패: {}", e.getMessage());
            return;
        }
        TextMessage msg = new TextMessage(json);
        sessions.removeIf(session -> {
            if (!session.isOpen()) return true;
            try {
                session.sendMessage(msg);
                return false;
            } catch (IOException e) {
                log.warn("[WS] 전송 실패, 세션 제거: {}", session.getId());
                return true;
            }
        });
    }

    private void sendTo(WebSocketSession session, Object payload) {
        try {
            String json = objectMapper.writeValueAsString(payload);
            session.sendMessage(new TextMessage(json));
        } catch (Exception e) {
            log.warn("[WS] 단일 전송 실패: {}", e.getMessage());
        }
    }
}
