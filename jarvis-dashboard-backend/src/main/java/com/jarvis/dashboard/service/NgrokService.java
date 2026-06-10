package com.jarvis.dashboard.service;

import com.jarvis.dashboard.entity.NgrokConfig;
import com.jarvis.dashboard.repository.NgrokConfigRepository;
import jakarta.annotation.PreDestroy;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.util.Optional;
import java.util.concurrent.TimeUnit;

@Service
@RequiredArgsConstructor
@Slf4j
public class NgrokService {

    private final NgrokConfigRepository ngrokConfigRepository;
    private Process ngrokProcess;

    // ── DB 조회 ───────────────────────────────────────────────────────────────

    public boolean isConfigured() {
        return ngrokConfigRepository.existsByActiveTrue();
    }

    public Optional<NgrokConfig> getActiveConfig() {
        return ngrokConfigRepository.findFirstByActiveTrueOrderByCreatedAtDesc();
    }

    // ── 설정 저장 + 터널 시작 ──────────────────────────────────────────────────

    @Transactional
    public NgrokConfig saveAndStart(String authToken, String domain) {
        // 기존 활성 설정 비활성화
        ngrokConfigRepository.findFirstByActiveTrueOrderByCreatedAtDesc()
            .ifPresent(cfg -> {
                cfg.setActive(false);
                ngrokConfigRepository.save(cfg);
            });

        // 신규 설정 저장
        NgrokConfig config = NgrokConfig.builder()
            .authToken(authToken)
            .domain(domain)
            .active(true)
            .build();
        NgrokConfig saved = ngrokConfigRepository.save(config);

        // 백그라운드 터널 시작
        startTunnel(authToken, domain);

        return saved;
    }

    // ── ngrok 프로세스 관리 ────────────────────────────────────────────────────

    public void startTunnel(String authToken, String domain) {
        stopTunnel(); // 기존 프로세스 정리

        try {
            // 1단계: Auth Token 등록
            log.info("[Ngrok] Auth Token 등록 중...");
            Process tokenProc = new ProcessBuilder(
                    resolveNgrokCmd(), "config", "add-authtoken", authToken)
                .redirectErrorStream(true)
                .start();

            // 10초 내 완료 대기
            if (!tokenProc.waitFor(10, TimeUnit.SECONDS)) {
                tokenProc.destroyForcibly();
                throw new RuntimeException("Auth Token 등록 타임아웃");
            }

            // 2단계: 터널 시작 (FastAPI 포트 8000)
            log.info("[Ngrok] 터널 시작 중 — domain: {}", domain);
            ngrokProcess = new ProcessBuilder(
                    resolveNgrokCmd(), "http",
                    "--domain=" + domain,
                    "8000")
                .redirectErrorStream(true)
                .start();

            // 비동기 로그 수집
            Thread logThread = new Thread(() -> {
                try (BufferedReader reader = new BufferedReader(
                        new InputStreamReader(ngrokProcess.getInputStream()))) {
                    String line;
                    while ((line = reader.readLine()) != null) {
                        log.debug("[Ngrok] {}", line);
                    }
                } catch (Exception ignored) {}
            });
            logThread.setDaemon(true);
            logThread.start();

            log.info("[Ngrok] 터널 프로세스 시작 완료 (PID: {})", ngrokProcess.pid());

        } catch (Exception e) {
            log.error("[Ngrok] 터널 시작 실패: {}", e.getMessage());
            throw new RuntimeException("ngrok 터널 시작 실패: " + e.getMessage(), e);
        }
    }

    public void stopTunnel() {
        if (ngrokProcess != null && ngrokProcess.isAlive()) {
            log.info("[Ngrok] 기존 터널 프로세스 종료");
            ngrokProcess.destroy();
            try {
                ngrokProcess.waitFor(3, TimeUnit.SECONDS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            ngrokProcess = null;
        }
    }

    public boolean isTunnelRunning() {
        return ngrokProcess != null && ngrokProcess.isAlive();
    }

    /** Windows / Mac / Linux 환경에 맞는 ngrok 실행 파일 경로. */
    private String resolveNgrokCmd() {
        String os = System.getProperty("os.name", "").toLowerCase();
        return os.contains("win") ? "ngrok.exe" : "ngrok";
    }

    @PreDestroy
    public void onShutdown() {
        stopTunnel();
    }
}
