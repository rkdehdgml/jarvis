package com.jarvis.dashboard.repository;

import com.jarvis.dashboard.entity.DailyLog;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import java.time.LocalDateTime;
import java.util.List;

public interface DailyLogRepository extends JpaRepository<DailyLog, Long> {
    List<DailyLog> findByCategoryOrderByCreatedAtDesc(String category, Pageable pageable);
    List<DailyLog> findByCreatedAtAfterOrderByCreatedAtDesc(LocalDateTime since);
    long countByCategory(String category);
}
