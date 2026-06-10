package com.jarvis.dashboard.controller;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/**
 * Electron main.js 헬스체크 폴링용 엔드포인트.
 * main.js 가 앱 시작 시 /api/health 를 30초간 폴링해 Spring Boot 준비 여부를 확인한다.
 */
@RestController
@RequestMapping("/api")
public class HealthController {

    @GetMapping("/health")
    public Map<String, String> health() {
        return Map.of("status", "UP");
    }
}
