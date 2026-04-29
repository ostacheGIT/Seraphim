import asyncio

from seraphim.engine import get_engine
from seraphim.engine.base import ChatMessage


async def main():
    messages: list[ChatMessage] = [
        {"role": "system", "content": "Tu es un assistant utile."},
        {"role": "user", "content": "Donne-moi une phrase très courte en français."},
    ]

    # Moteur par défaut
    default_engine = get_engine()
    result = await default_engine.chat(messages=messages)
    print("=== Default engine ===")
    for msg in result.get("messages", []):
        print(f"{msg['role']}: {msg['content']}")

    # Petit modèle (3B)
    small_engine = get_engine("ollama_qwen3b")
    res_small = await small_engine.chat(messages=messages)
    print("\n=== Engine ollama_qwen3b ===")
    for msg in res_small.get("messages", []):
        print(f"{msg['role']}: {msg['content']}")

    # Gros modèle (7B)
    big_engine = get_engine("ollama_qwen7b")
    res_big = await big_engine.chat(messages=messages)
    print("\n=== Engine ollama_qwen7b ===")
    for msg in res_big.get("messages", []):
        print(f"{msg['role']}: {msg['content']}")


if __name__ == "__main__":
    asyncio.run(main())