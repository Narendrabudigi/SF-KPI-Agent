import requests
import json
from rich.console import Console

import config

console = Console()

def _call_gemini(model: str, system_prompt: str, user_prompt: str) -> str:
    """Calls Google Gemini REST API"""
    if not config.GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is missing.")
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={config.GEMINI_API_KEY}"
    
    payload = {
        "system_instruction": {
            "parts": [{"text": system_prompt}]
        },
        "contents": [{
            "parts": [{"text": user_prompt}]
        }],
        "generationConfig": {
            "responseMimeType": "application/json"
        }
    }
    
    response = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=30)
    response.raise_for_status()
    
    data = response.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise ValueError(f"Unexpected response format from Gemini: {data}") from e


def _call_openai_compatible(url: str, api_key: str, model: str, system_prompt: str, user_prompt: str) -> str:
    """Calls OpenAI-compatible endpoints like Groq and OpenRouter"""
    if not api_key:
        raise ValueError("API Key is missing for this provider.")
        
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "response_format": {"type": "json_object"}
    }
    
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    
    data = response.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise ValueError(f"Unexpected response format: {data}") from e


def _route_provider(provider: str, model: str, system_prompt: str, user_prompt: str) -> str:
    """Routes the request to the correct API client implementation"""
    provider_lower = provider.lower()
    
    if "gemini" in provider_lower:
        return _call_gemini(model, system_prompt, user_prompt)
        
    elif "groq" in provider_lower:
        return _call_openai_compatible(
            url="https://api.groq.com/openai/v1/chat/completions",
            api_key=config.GROQ_API_KEY,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt
        )
        
    elif "openrouter" in provider_lower:
        return _call_openai_compatible(
            url="https://openrouter.ai/api/v1/chat/completions",
            api_key=config.OPENROUTER_API_KEY,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt
        )
        
    else:
        raise ValueError(f"Unknown provider configured: {provider}")


def generate_recommendation(system_prompt: str, user_prompt: str) -> str:
    """
    Attempts to generate a response iterating through the fallback chain.
    """
    # Define the fallback chain based on config
    chain = [
        {"tier": "PRIMARY", "provider": config.PRIMARY_PROVIDER, "model": config.PRIMARY_MODEL},
        {"tier": "SECONDARY", "provider": config.SECONDARY_PROVIDER, "model": config.SECONDARY_MODEL},
        {"tier": "THIRD", "provider": config.THIRD_PROVIDER, "model": config.THIRD_MODEL},
        {"tier": "FOURTH", "provider": config.FOURTH_PROVIDER, "model": config.FOURTH_MODEL},
    ]
    
    for step in chain:
        tier = step["tier"]
        provider = step["provider"]
        model = step["model"]
        
        if not provider or not model:
            continue
            
        console.print(f"[bold cyan][Prescriptive Agent][/bold cyan] Attempting {tier} LLM: [bold white]{provider.upper()}[/bold white] (Model: {model})")
        
        try:
            result = _route_provider(provider, model, system_prompt, user_prompt)
            console.print(f"[bold green][Prescriptive Agent] Successfully generated JSON response using {provider.upper()}![/bold green]")
            return result
            
        except Exception as e:
            console.print(f"[bold yellow][Prescriptive Agent] ⚠️ Failed using {provider.upper()} ({model}). Reason: {e}[/bold yellow]")
            console.print("[dim]Falling back to next provider in chain...[/dim]")
            
    # If we exhaust all providers
    console.print("[bold red][Prescriptive Agent] ❌ ALL LLM PROVIDERS IN THE FALLBACK CHAIN FAILED.[/bold red]")
    return None
