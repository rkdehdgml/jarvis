package com.jarvis.dashboard.controller;

import com.jarvis.dashboard.entity.DailyLog;
import com.jarvis.dashboard.repository.DailyLogRepository;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.PageRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/logs")
@RequiredArgsConstructor
public class DailyLogController {

    private final DailyLogRepository dailyLogRepository;

    @GetMapping
    public List<DailyLog> getRecent(
            @RequestParam(defaultValue = "100") int limit,
            @RequestParam(required = false) String category) {
        if (category != null && !category.isBlank()) {
            return dailyLogRepository.findByCategoryOrderByCreatedAtDesc(
                category.toUpperCase(), PageRequest.of(0, limit));
        }
        return dailyLogRepository.findAll(PageRequest.of(0, limit,
            org.springframework.data.domain.Sort.by("createdAt").descending())).getContent();
    }

    @GetMapping("/today")
    public List<DailyLog> getToday() {
        LocalDateTime startOfDay = LocalDateTime.now().toLocalDate().atStartOfDay();
        return dailyLogRepository.findByCreatedAtAfterOrderByCreatedAtDesc(startOfDay);
    }

    @PostMapping
    public DailyLog create(@Valid @RequestBody DailyLog log) {
        return dailyLogRepository.save(log);
    }

    @DeleteMapping("/{id}")
    public ResponseEntity<Void> delete(@PathVariable Long id) {
        if (!dailyLogRepository.existsById(id)) return ResponseEntity.notFound().build();
        dailyLogRepository.deleteById(id);
        return ResponseEntity.noContent().build();
    }

    @GetMapping("/stats")
    public Map<String, Long> stats() {
        return Map.of(
            "total",         dailyLogRepository.count(),
            "chat",          dailyLogRepository.countByCategory("CHAT"),
            "os_action",     dailyLogRepository.countByCategory("OS_ACTION"),
            "engine_switch", dailyLogRepository.countByCategory("ENGINE_SWITCH"),
            "error",         dailyLogRepository.countByCategory("ERROR")
        );
    }
}
