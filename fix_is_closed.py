paths = [
    r"C:\Users\Admin\.gemini\antigravity\scratch\atmani\bot.py",
    r"C:\Users\Admin\.gemini\antigravity\scratch\google-meet-bot\bot.py",
    r"C:\Users\Admin\Desktop\last\bot.py"
]

target = "if page.is_closed() or (current_context_ref and current_context_ref.is_closed()):"
replacement = "if page.is_closed():"

for p in paths:
    with open(p, "r", encoding="utf-8") as f:
        content = f.read()

    if target in content:
        content = content.replace(target, replacement)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Fixed is_closed in {p}")
    else:
        print(f"Target not found in {p}")

print("Done fixing BrowserContext.is_closed error!")
