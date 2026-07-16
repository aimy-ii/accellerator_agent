# scratch_stream.py в корне агента
import asyncio
from src.techspec.service import generate_spec

async def main():
    calls = []
    def on_delta(d):
        calls.append(d)
        print(f"delta #{len(calls)} len={len(d)}: {d[:40]!r}")
    spec = await generate_spec(
        [{"role": "user", "content": "Лендинг для кофейни: меню, форма брони, карта проезда."}],
        creative=True, missing_topics=[], on_delta=on_delta,
    )
    print(f"\nВСЕГО дельт: {len(calls)}, суммарно символов: {sum(len(c) for c in calls)}")
    print(f"Длина итогового tech_spec_text: {len(spec.tech_spec_text)}")

asyncio.run(main())