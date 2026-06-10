package com.jarvis.dashboard.controller;

import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

import java.util.Map;

/**
 * FastAPI(port 8000) AI 엔드포인트를 Spring Boot(port 8080)에서 프록시.
 *
 * Electron renderer.js 는 BASE_URL=localhost:8080 만 바라보므로,
 * 모든 /api/chat/* 와 /api/os/* 요청을 FastAPI 로 투명하게 전달한다.
 */
@RestController
@RequiredArgsConstructor
public class ChatProxyController {

    private final WebClient coreAiWebClient;

    // ── /api/chat ──────────────────────────────────────────────────────────────

    /** LLM 스트리밍 응답 */
    @PostMapping(value = "/api/chat/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public Flux<String> chatStream(@RequestBody Map<String, Object> body) {
        return coreAiWebClient.post()
            .uri("/api/chat/stream")
            .contentType(MediaType.APPLICATION_JSON)
            .bodyValue(body)
            .retrieve()
            .onStatus(status -> status.is5xxServerError(),
                      response -> response.bodyToMono(String.class)
                          .map(err -> new RuntimeException("FastAPI 오류: " + err)))
            .onStatus(status -> status.is4xxClientError(),
                      response -> response.bodyToMono(String.class)
                          .map(err -> new RuntimeException("요청 오류: " + err)))
            .bodyToFlux(String.class)
            .onErrorResume(ex -> Flux.just(
                "[자비스 오류] AI 서버 연결 실패. FastAPI(:8000)가 실행 중인지 확인하세요. 오류: "
                + ex.getMessage()));
    }

    /** 에이전트 분류만 수행 (실행 없음) */
    @PostMapping("/api/chat/classify")
    public Mono<Object> classify(@RequestBody Map<String, Object> body) {
        return coreAiWebClient.post()
            .uri("/api/chat/classify")
            .contentType(MediaType.APPLICATION_JSON)
            .bodyValue(body)
            .retrieve()
            .bodyToMono(Object.class);
    }

    /** 등록된 에이전트 목록 */
    @GetMapping("/api/chat/agents")
    public Mono<Object> agents() {
        return coreAiWebClient.get()
            .uri("/api/chat/agents")
            .retrieve()
            .bodyToMono(Object.class);
    }

    /** 현재 활성 엔진 조회 */
    @GetMapping("/api/chat/engine")
    public Mono<Object> getEngine() {
        return coreAiWebClient.get()
            .uri("/api/chat/engine")
            .retrieve()
            .bodyToMono(Object.class);
    }

    /** 전체 엔진 목록 조회 */
    @GetMapping("/api/chat/engines")
    public Mono<Object> getEngines() {
        return coreAiWebClient.get()
            .uri("/api/chat/engines")
            .retrieve()
            .bodyToMono(Object.class);
    }

    /** 엔진 전환 */
    @PutMapping("/api/chat/engine/{key}")
    public Mono<Object> switchEngine(@PathVariable String key) {
        return coreAiWebClient.put()
            .uri("/api/chat/engine/{key}", key)
            .retrieve()
            .bodyToMono(Object.class);
    }

    /** 음성 명령에서 엔진 전환 감지 */
    @PostMapping("/api/chat/engine/detect")
    public Mono<Object> detectEngine(@RequestBody Map<String, Object> body) {
        return coreAiWebClient.post()
            .uri("/api/chat/engine/detect")
            .contentType(MediaType.APPLICATION_JSON)
            .bodyValue(body)
            .retrieve()
            .bodyToMono(Object.class);
    }

    // ── /api/os ────────────────────────────────────────────────────────────────

    /** OS 액션 플랜 생성 (실행 없음) */
    @PostMapping("/api/os/plan")
    public Mono<Object> osPlan(@RequestBody Map<String, Object> body) {
        return coreAiWebClient.post()
            .uri("/api/os/plan")
            .contentType(MediaType.APPLICATION_JSON)
            .bodyValue(body)
            .retrieve()
            .bodyToMono(Object.class);
    }

    /** OS 명령 실행 (NDJSON 스트리밍) */
    @PostMapping(value = "/api/os/run", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public Flux<String> osRun(@RequestBody Map<String, Object> body) {
        return coreAiWebClient.post()
            .uri("/api/os/run")
            .contentType(MediaType.APPLICATION_JSON)
            .bodyValue(body)
            .retrieve()
            .bodyToFlux(String.class);
    }

    /** 기존 플랜 직접 실행 */
    @PostMapping(value = "/api/os/execute", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public Flux<String> osExecute(@RequestBody Map<String, Object> body) {
        return coreAiWebClient.post()
            .uri("/api/os/execute")
            .contentType(MediaType.APPLICATION_JSON)
            .bodyValue(body)
            .retrieve()
            .bodyToFlux(String.class);
    }

    // ── /api/speech ────────────────────────────────────────────────────────────

    /** Whisper 음성 인식 */
    @PostMapping("/api/speech/transcribe")
    public Mono<Object> transcribe(@RequestBody Map<String, Object> body) {
        return coreAiWebClient.post()
            .uri("/api/speech/transcribe")
            .contentType(MediaType.APPLICATION_JSON)
            .bodyValue(body)
            .retrieve()
            .bodyToMono(Object.class);
    }
}
