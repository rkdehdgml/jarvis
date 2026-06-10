package com.jarvis.dashboard.controller;

import com.jarvis.dashboard.entity.Task;
import com.jarvis.dashboard.repository.TaskRepository;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/tasks")
@RequiredArgsConstructor
public class TaskController {

    private final TaskRepository taskRepository;

    @GetMapping
    public List<Task> getAll() {
        return taskRepository.findAll();
    }

    @PostMapping
    public Task create(@Valid @RequestBody Task task) {
        return taskRepository.save(task);
    }

    @PutMapping("/{id}")
    public ResponseEntity<Task> update(@PathVariable Long id, @RequestBody Task patch) {
        return taskRepository.findById(id)
            .map(t -> {
                if (patch.getTitle()       != null) t.setTitle(patch.getTitle());
                if (patch.getDescription() != null) t.setDescription(patch.getDescription());
                if (patch.getPriority()    != null) t.setPriority(patch.getPriority());
                if (patch.getStatus()      != null) t.setStatus(patch.getStatus());
                if (patch.getProject()     != null) t.setProject(patch.getProject());
                if (patch.getDeadline()    != null) t.setDeadline(patch.getDeadline());
                return ResponseEntity.ok(taskRepository.save(t));
            })
            .orElse(ResponseEntity.notFound().build());
    }

    @DeleteMapping("/{id}")
    public ResponseEntity<Void> delete(@PathVariable Long id) {
        if (!taskRepository.existsById(id)) return ResponseEntity.notFound().build();
        taskRepository.deleteById(id);
        return ResponseEntity.noContent().build();
    }
}
