package com.jarvis.dashboard.entity;

import jakarta.persistence.*;
import lombok.*;
import java.time.LocalDateTime;

@Entity
@Table(name = "daily_logs", indexes = {
    @Index(name = "idx_daily_log_category",   columnList = "category"),
    @Index(name = "idx_daily_log_created_at", columnList = "created_at"),
})
@Getter @Setter @NoArgsConstructor @AllArgsConstructor @Builder
public class DailyLog {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    /** 로그 분류: CHAT | OS_ACTION | SYSTEM | ENGINE_SWITCH | ERROR */
    @Column(nullable = false, length = 32)
    private String category;

    @Column(columnDefinition = "TEXT", nullable = false)
    private String content;

    /** 사용된 에이전트 페르소나 키 (nullable) */
    @Column(name = "agent_key", length = 64)
    private String agentKey;

    /** 사용된 LLM 엔진 프리셋 키 (nullable) */
    @Column(name = "engine_key", length = 64)
    private String engineKey;

    /** 관련 메타데이터 JSON 문자열 (nullable) */
    @Column(columnDefinition = "TEXT")
    private String metadata;

    @Column(name = "created_at", updatable = false)
    private LocalDateTime createdAt;

    @PrePersist
    void prePersist() {
        createdAt = LocalDateTime.now();
    }

    public enum Category {
        CHAT, OS_ACTION, SYSTEM, ENGINE_SWITCH, ERROR
    }
}
