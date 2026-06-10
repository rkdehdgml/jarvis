package com.jarvis.dashboard.controller;

import com.jarvis.dashboard.entity.NgrokConfig;
import com.jarvis.dashboard.service.NgrokService;
import com.jarvis.dashboard.websocket.JarvisStatusHandler;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import lombok.Data;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/api/ngrok")
@RequiredArgsConstructor
public class NgrokController {

    private final NgrokService ngrokService;
    private final JarvisStatusHandler wsHandler;

    // ── 설정 조회 (Electron 최초 실행 시 호출) ────────────────────────────────

    @GetMapping("/config")
    public Map<String, Object> getConfig() {
        var optConfig = ngrokService.getActiveConfig();
        if (optConfig.isEmpty()) {
            return Map.of(
                "configured",     false,
                "tunnel_running", false
            );
        }
        NgrokConfig cfg = optConfig.get();
        return Map.of(
            "configured",     true,
            "domain",         cfg.getDomain(),
            "tunnel_running", ngrokService.isTunnelRunning(),
            "created_at",     cfg.getCreatedAt().toString()
        );
    }

    // ── 최초 설정 저장 + 터널 즉시 시작 ──────────────────────────────────────

    @PostMapping("/config")
    public ResponseEntity<?> saveConfig(@Valid @RequestBody NgrokSetupRequest req) {
        try {
            NgrokConfig saved = ngrokService.saveAndStart(req.getAuthToken(), req.getDomain());

            // Electron으로 ngrok 준비 완료 브로드캐스트
            wsHandler.broadcastNgrokReady(saved.getDomain());

            return ResponseEntity.ok(Map.of(
                "success",    true,
                "domain",     saved.getDomain(),
                "message",    "ngrok 터널이 시작되었습니다: " + saved.getDomain()
            ));
        } catch (Exception e) {
            return ResponseEntity.internalServerError().body(Map.of(
                "success", false,
                "error",   e.getMessage()
            ));
        }
    }

    // ── 터널 수동 재시작 ───────────────────────────────────────────────────────

    @PostMapping("/restart")
    public ResponseEntity<?> restartTunnel() {
        return ngrokService.getActiveConfig()
            .map(cfg -> {
                try {
                    ngrokService.startTunnel(cfg.getAuthToken(), cfg.getDomain());
                    wsHandler.broadcastNgrokReady(cfg.getDomain());
                    return ResponseEntity.ok(Map.of("success", true, "domain", cfg.getDomain()));
                } catch (Exception e) {
                    return ResponseEntity.internalServerError()
                        .body(Map.of("success", false, "error", e.getMessage()));
                }
            })
            .orElse(ResponseEntity.badRequest()
                .body(Map.of("success", false, "error", "저장된 ngrok 설정이 없습니다.")));
    }

    // ── 터널 상태 조회 ────────────────────────────────────────────────────────

    @GetMapping("/status")
    public Map<String, Object> status() {
        return Map.of(
            "tunnel_running",   ngrokService.isTunnelRunning(),
            "configured",       ngrokService.isConfigured(),
            "ws_clients",       wsHandler.getConnectedCount()
        );
    }

    // ── 요청 DTO ──────────────────────────────────────────────────────────────

    @Data
    public static class NgrokSetupRequest {
        @NotBlank(message = "Auth Token은 필수입니다.")
        private String authToken;

        @NotBlank(message = "도메인은 필수입니다.")
        private String domain;
    }
}
