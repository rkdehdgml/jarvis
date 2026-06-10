package com.jarvis.dashboard.repository;

import com.jarvis.dashboard.entity.ChatMessage;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.domain.Pageable;
import java.util.List;

public interface ChatMessageRepository extends JpaRepository<ChatMessage, Long> {
    List<ChatMessage> findAllByOrderByCreatedAtAsc();
    List<ChatMessage> findTop50ByOrderByCreatedAtDesc(Pageable pageable);
}
