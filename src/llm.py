import os
import time
import requests
from typing import List, Dict, Any, Optional
from openai import OpenAI
from src.config import AppConfig

class LLMClient:
    """Wrapper around OpenAI-compatible API (SenseNova, OpenAI, etc.) with built-in retry and backoff."""
    
    def __init__(self, config: AppConfig):
        self.config = config
        self.provider = config.ai.default_provider
        self.api_key = config.ai.api_key
        self.base_url = config.ai.base_url
        self.model_v3 = config.ai.model_v3
        self.model_r1 = config.ai.model_r1
        
        # Check API key presence
        if not self.api_key:
            print(f"[LLM Warning] No API key found for provider '{self.provider}'. LLM requests will fail unless mock runs are used.")
            
        # Initialize OpenAI client (SenseNova/OpenAI are fully compatible)
        # Note: We use the standard synchronous OpenAI client
        if self.api_key:
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url
            )
        else:
            self.client = None

    def call_llm(
        self, 
        messages: List[Dict[str, str]], 
        use_reasoning: bool = False,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        retries: int = 5,
        backoff_factor: float = 2.0
    ) -> Dict[str, Any]:
        """Calls the LLM with robust error-handling, backoff, and rate-limiting support.
        
        Args:
            messages: List of chat message dicts (role, content)
            use_reasoning: If True, uses the reasoning model (R1). Else uses V3.
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            retries: Number of retries on rate limit (429) or temporary server errors
            backoff_factor: Multiplier for backoff delay
        """
        if not self.client:
            print("[LLM Error] Cannot call LLM: API key is not configured.")
            return {"content": "Error: LLM API key not configured.", "reasoning": None}
            
        model = self.model_r1 if use_reasoning else self.model_v3
        temp = temperature if temperature is not None else self.config.ai.temperature
        max_t = max_tokens if max_tokens is not None else self.config.ai.max_tokens
        
        # SenseNova DeepSeek-R1 does not support temperature settings in some cases or custom params
        # We handle this gracefully: R1 prefers temperature=None or 1.0 (some endpoints error on low temperature)
        kwargs = {
            "model": model,
            "messages": messages,
            "max_tokens": max_t,
        }
        
        # For non-reasoning models (V3-1), we can set custom temperature
        if not use_reasoning:
            kwargs["temperature"] = temp
            
        delay = self.config.ai.rate_limit_delay
        
        for attempt in range(retries):
            try:
                # Active rate control: force a brief delay before each API call to respect the QPS/RPM
                if attempt > 0:
                    time.sleep(delay * (backoff_factor ** (attempt - 1)))
                
                print(f"[LLM] Requesting model={model} (Attempt {attempt + 1}/{retries})...")
                
                response = self.client.chat.completions.create(**kwargs)
                
                # Extract text and optional reasoning_content (DeepSeek specific)
                choice = response.choices[0]
                content = choice.message.content or ""
                
                # Check for reasoning_content (either direct field or additional_kwargs)
                reasoning = None
                if hasattr(choice.message, "reasoning_content"):
                    reasoning = getattr(choice.message, "reasoning_content")
                elif hasattr(choice.message, "model_extra") and choice.message.model_extra:
                    reasoning = choice.message.model_extra.get("reasoning_content")
                
                # Respect rate limiting after successful calls as well
                time.sleep(self.config.ai.rate_limit_delay / 2.0)
                
                return {
                    "content": content,
                    "reasoning": reasoning,
                    "model": model
                }
                
            except Exception as e:
                err_msg = str(e)
                print(f"[LLM Warning] Attempt {attempt + 1} failed: {err_msg}")
                
                # If it's a rate limit error (429), try backing off
                if "429" in err_msg or "rate limit" in err_msg.lower() or "too many requests" in err_msg.lower():
                    # We back off and retry
                    continue
                # If it's a model parameters issue with reasoning models (e.g. temperature), remove temperature and retry
                elif "temperature" in err_msg.lower() and use_reasoning and "temperature" in kwargs:
                    print("[LLM Info] Retrying R1 without temperature parameter...")
                    del kwargs["temperature"]
                    continue
                else:
                    # For other errors, sleep briefly and retry
                    if attempt == retries - 1:
                        raise e
                    time.sleep(2)
                    
        raise RuntimeError("LLM request failed after maximum retries due to persistent rate limiting.")
