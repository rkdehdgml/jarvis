package com.jarvis.dashboard.config;

import com.jarvis.dashboard.websocket.JarvisStatusHandler;
import lombok.RequiredArgsConstructor;
import org.springframework.context.annotation.Configuration;
import org.springframework.messaging.simp.config.MessageBrokerRegistry;
import org.springframework.web.socket.config.annotation.*;

/**
 * WebSocket 설정 — 두 채널을 함께 등록:
 *   /ws          → STOMP (Spring 메시지 브로커 — 서버→클라이언트 토픽 구독)
 *   /ws-status   → Raw WebSocket (Electron HUD 실시간 상태 동기화)
 */
@Configuration
@EnableWebSocketMessageBroker
@EnableWebSocket
@RequiredArgsConstructor
public class WebSocketConfig implements WebSocketMessageBrokerConfigurer, WebSocketConfigurer {

    private final JarvisStatusHandler jarvisStatusHandler;

    // ── STOMP 설정 ────────────────────────────────────────────────────────────

    @Override
    public void registerStompEndpoints(StompEndpointRegistry registry) {
        registry.addEndpoint("/ws")
                .setAllowedOriginPatterns("*")
                .withSockJS();
    }

    @Override
    public void configureMessageBroker(MessageBrokerRegistry registry) {
        registry.enableSimpleBroker("/topic");
        registry.setApplicationDestinationPrefixes("/app");
    }

    // ── Raw WebSocket (Electron HUD) ─────────────────────────────────────────

    @Override
    public void registerWebSocketHandlers(WebSocketHandlerRegistry registry) {
        registry.addHandler(jarvisStatusHandler, "/ws-status")
                .setAllowedOriginPatterns("*");
    }
}
