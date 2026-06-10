package com.jarvis.dashboard.entity;

import jakarta.persistence.*;
import lombok.*;
import java.time.LocalDateTime;

@Entity
@Table(name = "ngrok_configs")
@Getter @Setter @NoArgsConstructor @AllArgsConstructor @Builder
public class NgrokConfig {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    /** ngrok Auth Token */
    @Column(name = "auth_token", nullable = false, length = 256)
    private String authToken;

    /** ngrok 고정 도메인 (예: my-jarvis.ngrok-free.app) */
    @Column(name = "domain", nullable = false, length = 256)
    private String domain;

    /** 현재 활성 설정 여부 */
    @Column(nullable = false)
    private boolean active;

    @Column(name = "created_at", updatable = false)
    private LocalDateTime createdAt;

    @Column(name = "updated_at")
    private LocalDateTime updatedAt;

    @PrePersist
    void prePersist() {
        createdAt = updatedAt = LocalDateTime.now();
    }

    @PreUpdate
    void preUpdate() {
        updatedAt = LocalDateTime.now();
    }
}
