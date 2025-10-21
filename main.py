import os
import asyncio
from typing import Dict, Optional, Tuple
import discord
from discord.ext import commands
from dotenv import load_dotenv

# ==============================
# 設定読み込み
# ==============================
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
REMINDER_MINUTES = int(os.getenv("REMINDER_MINUTES", "5"))
REPEAT_INTERVAL_MINUTES = int(os.getenv("REPEAT_INTERVAL_MINUTES", "30"))
COUNT_DEAF_AS_MUTE = os.getenv("COUNT_DEAF_AS_MUTE", "1") == "1"
FALLBACK_TEXT_CHANNEL_ID = int(os.getenv("FALLBACK_TEXT_CHANNEL_ID", "0"))

# ==============================
# Discord設定
# ==============================
intents = discord.Intents.default()
intents.members = True
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents)

MuteKey = Tuple[int, int]
mute_tasks: Dict[MuteKey, asyncio.Task] = {}

# ==============================
# ユーティリティ
# ==============================
def is_muted_state(vs: Optional[discord.VoiceState]) -> bool:
    """ミュート状態を判定"""
    if not vs:
        return False
    muted = vs.self_mute or vs.mute
    if COUNT_DEAF_AS_MUTE:
        muted = muted or vs.self_deaf or vs.deaf
    return muted

async def get_messageable_for_voice(vc: Optional[discord.VoiceChannel]) -> Optional[discord.abc.Messageable]:
    """Text-in-Voiceが使えるか確認し、なければフォールバック"""
    if vc is not None:
        try:
            return vc
        except Exception:
            pass
        guild = vc.guild
    else:
        guild = None
    if FALLBACK_TEXT_CHANNEL_ID and guild:
        ch = guild.get_channel(FALLBACK_TEXT_CHANNEL_ID)
        if isinstance(ch, discord.TextChannel):
            return ch
    return None

# ==============================
# ミュート検知タスク（5分後に通知→以後30分毎に再通知）
# ==============================
async def reminder_task(guild_id: int, member_id: int, vc_id_snapshot: Optional[int]) -> None:
    try:
        print(f"[TASK START] guild={guild_id}, member={member_id}")
        await asyncio.sleep(REMINDER_MINUTES * 60)

        while True:
            guild = bot.get_guild(guild_id)
            if not guild:
                print(f"[TASK END] guild={guild_id} 不明 → 終了")
                return
            member = guild.get_member(member_id)
            if not member:
                print(f"[TASK END] member={member_id} 不明 → 終了")
                return

            vs = member.voice
            if not vs or not is_muted_state(vs):
                print(f"[TASK END] {member.display_name} 解除済み → 終了")
                return

            vc_now: Optional[discord.VoiceChannel] = (
                vs.channel if isinstance(vs.channel, discord.VoiceChannel) else None
            )
            dest = await get_messageable_for_voice(vc_now)
            if dest:
                text = f"{member.display_name} さんは {REMINDER_MINUTES} 分以上ミュートです。次回通知は {REPEAT_INTERVAL_MINUTES} 分後です。"
                print(f"[NOTIFY] {text}")
                await dest.send(text)

            # 30分ごと再通知（途中で解除されたら終了）
            check_interval = 10
            loops = max(1, (REPEAT_INTERVAL_MINUTES * 60) // check_interval)
            for _ in range(loops):
                await asyncio.sleep(check_interval)
                vs = member.voice
                if not vs or not is_muted_state(vs):
                    print(f"[TASK END] {member.display_name} 中途解除 → 終了")
                    return
    finally:
        mute_tasks.pop((guild_id, member_id), None)
        print(f"[TASK CLEANUP] guild={guild_id}, member={member_id}")

def start_or_replace_timer(member: discord.Member) -> None:
    """タイマーを開始または置き換え"""
    key: MuteKey = (member.guild.id, member.id)
    prev = mute_tasks.get(key)
    if prev and not prev.done():
        prev.cancel()
    vc_id = member.voice.channel.id if (member.voice and member.voice.channel) else None
    mute_tasks[key] = asyncio.create_task(reminder_task(member.guild.id, member.id, vc_id))

def cancel_timer(member: discord.Member) -> None:
    """タイマーを停止"""
    key: MuteKey = (member.guild.id, member.id)
    task = mute_tasks.pop(key, None)
    if task and not task.done():
        task.cancel()

# ==============================
# イベント
# ==============================
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (latency={bot.latency:.3f}s)")
    print(f"Guilds: {[g.name for g in bot.guilds]}")
    print("Bot is online and monitoring VC states.")

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    was_muted = is_muted_state(before)
    now_muted = is_muted_state(after)
    print(f"[VOICE UPDATE] {member.display_name}: was_muted={was_muted}, now_muted={now_muted}")

    # 即時通知はなし（開始/解除メッセージは送らない）
    if now_muted and not was_muted:
        print(f"[TIMER START] {member.display_name}")
        start_or_replace_timer(member)
    elif not now_muted and was_muted:
        print(f"[TIMER CANCEL] {member.display_name}")
        cancel_timer(member)

# ==============================
# コマンド（確認用）
# ==============================
@bot.command()
@commands.has_permissions(manage_messages=True)
async def muted(ctx: commands.Context):
    """現在のVCでミュートしている人を列挙"""
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.reply("ボイスチャンネルにさんかしてください。")
        return
    vc: discord.VoiceChannel = ctx.author.voice.channel  # type: ignore
    ms = [m for m in vc.members if is_muted_state(m.voice)]
    if not ms:
        await ctx.reply("このVCでミュートの人はいません。")
        return
    names = ", ".join(m.display_name for m in ms)
    await ctx.reply(f"ミュート中: {names}")

# ==============================
# 実行
# ==============================
if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN が設定されていません。")
    bot.run(TOKEN)

