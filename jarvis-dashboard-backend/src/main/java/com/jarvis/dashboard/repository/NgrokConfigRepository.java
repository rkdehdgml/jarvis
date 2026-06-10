package com.jarvis.dashboard.repository;

import com.jarvis.dashboard.entity.NgrokConfig;
import org.springframework.data.jpa.repository.JpaRepository;
import java.util.Optional;

public interface NgrokConfigRepository extends JpaRepository<NgrokConfig, Long> {
    Optional<NgrokConfig> findFirstByActiveTrueOrderByCreatedAtDesc();
    boolean existsByActiveTrue();
}
