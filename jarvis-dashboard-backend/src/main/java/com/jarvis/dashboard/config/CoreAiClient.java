package com.jarvis.dashboard.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.reactive.function.client.WebClient;

@Configuration
public class CoreAiClient {

    @Value("${jarvis.core-ai-url}")
    private String coreAiUrl;

    @Bean
    public WebClient coreAiWebClient() {
        return WebClient.builder()
                .baseUrl(coreAiUrl)
                .build();
    }
}
