from __future__ import annotations

"""User-visible text preserved from the original bot."""

from html import escape

START_TEXT = (
    "欢迎使用 mizuki bot 喵～！\n\n"
    "把想归档的链接直接丢给我就好喵！\n"
    "目前支持 X/Twitter、Pixiv、Poipiku 等站点。\n\n"
    "也可以直接发送图片，mizuki 会努力帮你找来源喵～\n\n"
    "管理员投稿会直接发布到频道，普通用户投稿会先进入审核喵。\n"
    "请放心投喂喵～！维护请找会上网的家猫 @kotokotori 喵～"
)

HELP_TEXT = (
    "📖 mizuki 使用小纸条喵～\n\n"
    "把 X/Twitter、Pixiv、Poipiku 链接直接丢给我就可以投稿啦喵。\n"
    "一次发好几个链接也没问题，我会乖乖排队处理喵。\n\n"
    "想取回频道里发过的原图，用 /original 看说明就好喵。"
)

ADMIN_HELP_TEXT = (
    "🛠 管理员小工具箱喵～\n\n"
    "/pending - 看看有没有等摸头审核的投稿喵\n"
    "/find <id|url> - 查询投稿记录喵\n"
    "/retry <id|url> - 重新下载/发布投稿喵\n"
    "/delete <id|url> - 删除投稿并解除查重喵\n"
    "/stats - 查看投稿统计喵\n"
    "/config - 偷看当前配置喵\n"
    "/set <key> <value> - 调整配置喵\n"
    "/pixiv_status - 查看 Pixiv 下载额度喵\n"
    "/nsfw_threshold <low> <high> - 调整 NSFW 判断阈值喵\n"
    "/bookmark_watch - 开始盯 Twitter bookmark 喵\n\n"
    "/find <id|url> - 查询投稿记录喵\n"
    "/retry <id|url> - 重新下载/发布投稿喵\n"
    "/delete <id|url> - 删除投稿并解除查重喵\n"
    "/stats - 查看投稿统计喵\n\n"
    "频道消息转发给我时，还可以移动频道或删除投稿喵。"
)

ORIGINAL_HELP_TEXT = (
    "想取回原图的话，把频道里的消息转发给我，或者直接发送已经投稿过的原链接/频道消息链接喵。"
)

PERMISSION_DENIED = "呜喵...你没有权限做这个操作喵😾"
SET_USAGE = "用法喵：/set <key> <value>"
NO_PENDING = "太棒啦！目前没有待审核的投稿喵🥳"
NO_SUPPORTED_LINK = "呜喵...没有识别到支持的链接喵😿\n直接发送链接/转发带链接的消息都可以哦～"
PIXIV_RATE_LIMITED = "😿 呜喵...Pixiv下载次数达到上限啦喵！5小时内最多下载100次哦，稍后再试吧～"
BOOKMARK_WATCH_UNAVAILABLE = "😿 Twitter bookmark 监控还没配置好喵，请先设置 token 和 user id。"
BOOKMARK_WATCH_STARTED = "✅ Twitter bookmark 监控已开启喵！每30秒检查一次，5分钟没有更新会自动关闭。"
BOOKMARK_WATCH_STOPPED_IDLE = "⏸ Twitter bookmark 监控已自动关闭喵：5分钟没有更新。"
BOOKMARK_WATCH_STOPPED_CREDITS = "⏸ Twitter bookmark 监控已停止喵：X API credits 不足，请补充后再开启。"
BOOKMARK_WATCH_FORBIDDEN = "呜喵...你没有权限做这个操作喵😾"
ADMIN_ERROR_PREFIX = "⚠️ Bot 报错喵"

APPROVE_BUTTON = "✅ 通过"
REJECT_BUTTON = "❌ 拒绝"
MOVE_TO_R18_BUTTON = "转到色图频道"
MOVE_TO_SAFE_BUTTON = "转到不色频道"
DELETE_POST_BUTTON = "删除推文"


def set_success(key: str, value: str) -> str:
    return f"好哒！已经设置 {key} = {value} 喵✅"


def config_header() -> str:
    return "📋 当前配置喵：\n"


def pending_header() -> str:
    return "📝 待审核列表喵：\n"


def pixiv_status(count: int, first_time: str | None, last_time: str | None) -> str:
    remaining = 100 - count
    text = "📊 Pixiv下载频率使用情况喵：\n"
    text += f"当前5小时周期内已使用：{count}/100次\n"
    text += f"剩余可用次数：{remaining}次\n"
    if first_time:
        text += f"周期开始时间：{first_time}\n"
    if last_time:
        text += f"最近一次请求：{last_time}\n"
    return text


def nsfw_threshold_status(low: float, high: float) -> str:
    return f"📊 当前NSFW阈值喵：low={low:.2f}, high={high:.2f}"


def nsfw_threshold_usage() -> str:
    return "用法喵：/nsfw_threshold <low> <high>"


def nsfw_threshold_updated(low: float, high: float) -> str:
    return f"好哒！NSFW阈值已更新喵：low={low:.2f}, high={high:.2f}"


def admin_lookup_usage(command: str) -> str:
    return f"用法喵：/{command} <submission_id 或链接>"


def submission_not_found(target: str) -> str:
    return f"呜喵...没有找到这个投稿喵：{target}"


def submission_summary(submission, metadata: dict) -> str:
    text = f"📦 投稿 #{submission.id}\n"
    text += f"状态：{submission.status}\n"
    text += f"用户：{submission.username} ({submission.user_id})\n"
    text += f"链接：{submission.url}\n"
    text += f"canonical：{submission.canonical_url or metadata.get('canonical_url') or ''}\n"
    text += f"provider：{submission.provider or ''}\n"
    text += f"媒体数：{len(submission.media_paths)}\n"
    text += f"频道消息：{submission.message_id or ''}\n"
    if metadata.get("channel_message_ids"):
        text += f"频道消息组：{metadata.get('channel_message_ids')}\n"
    if metadata.get("channel_id"):
        text += f"频道：{metadata.get('channel_id')}\n"
    if metadata.get("safety_rating"):
        text += f"NSFW：{metadata.get('safety_rating')} score={metadata.get('safety_score', 'n/a')} class={metadata.get('safety_class', 'n/a')}\n"
    if submission.created_at:
        text += f"创建：{submission.created_at}\n"
    if submission.updated_at:
        text += f"更新：{submission.updated_at}\n"
    return text


def retry_started(submission_id: int, url: str) -> str:
    return f"收到喵，开始重新处理投稿 #{submission_id}：{url}"


def retry_failed(submission_id: int, url: str) -> str:
    return f"呜喵...投稿 #{submission_id} 重新下载失败了喵：{url}"


def retry_pending(submission_id: int) -> str:
    return f"投稿 #{submission_id} 已重新下载，进入审核喵。"


def retry_published(submission_id: int) -> str:
    return f"投稿 #{submission_id} 已重新下载并发布喵。"


def delete_success(submission_id: int) -> str:
    return f"投稿 #{submission_id} 已删除并解除查重喵。"


def stats_summary(stats: dict[str, int]) -> str:
    return (
        "📊 投稿统计喵：\n"
        f"今日：{stats.get('today', 0)}\n"
        f"近7天：{stats.get('week', 0)}\n"
        f"待审核：{stats.get('pending', 0)}\n"
        f"已发布：{stats.get('approved', 0)}\n"
        f"已拒绝：{stats.get('rejected', 0)}\n"
        f"已删除：{stats.get('deleted', 0)}"
    )


def original_found(url: str) -> str:
    return f"😺 找到投稿原图啦喵！正在发送...\n原链接: {url}"


def duplicate_submission(submission_id: int, status: str) -> str:
    return f"😺 这个链接已经投过稿啦喵！投稿ID #{submission_id}，当前状态：{status}"


def found_links(count: int) -> str:
    return f"哇哦！识别到 {count} 个链接喵～ 开始处理啦🥳"


def processing_link(index: int, total: int, url: str) -> str:
    return f"正在处理第 {index}/{total} 个链接喵：{url}"


def download_failed(url: str) -> str:
    return f"呜喵...下载失败了喵😿：{url}\n主人可以检查一下链接是不是有效哦～"


def duplicate_insert_failed() -> str:
    return "😿 投稿失败了喵，这个链接已经存在哦～"


def admin_published(url: str) -> str:
    return f"太棒啦！已经成功发布到频道啦喵🥳：{url}"


def submitted_for_review(url: str) -> str:
    return f"投稿已提交成功喵😺！等待管理员审核中哦：{url}"


def review_submit_failed(url: str) -> str:
    return f"呜喵...投稿提交失败了喵😿，请稍后重试哦：{url}"


def review_caption(submission_id: int, username: str, url: str, metadata: dict) -> str:
    text = f"新投稿 #{submission_id}\n用户：{username}\n\n"
    author_name = metadata.get("author_name", "")
    title = metadata.get("title", "")
    content_text = metadata.get("text", "")
    canonical_url = metadata.get("canonical_url", url)
    if author_name:
        text += f"{author_name}：\n\n"
    if title:
        text += f"{title}\n\n"
    if content_text:
        text += f"{content_text}\n\n"
    if metadata.get("safety_rating") == "uncertain":
        reason = metadata.get("safety_reason") or "需要人工判断"
        score = metadata.get("safety_score")
        score_text = f"，score={score:.2f}" if isinstance(score, (int, float)) else ""
        text += f"⚠️ R-18 自动判断不确定（{reason}{score_text}），请审核喵。\n\n"
    elif metadata.get("safety_rating") == "r18":
        reason = metadata.get("safety_reason") or "命中 R-18 规则"
        text += f"🔞 R-18：{reason}\n\n"
    text += f"{canonical_url}"
    return text


def publish_caption(
    url: str,
    *,
    author_name: str | None = None,
    text: str | None = None,
    canonical_url: str | None = None,
) -> str:
    caption = ""
    content_part = ""
    if text:
        cleaned_text = text.replace("\n", " ").replace("\r", "").strip()
        content_part = f"「{escape(cleaned_text)}」"
    if author_name:
        caption += f"<b>{escape(author_name)}</b>: {content_part}"
    else:
        caption += content_part
    caption += "\n"
    if canonical_url:
        caption += f"{escape(canonical_url)}"
    else:
        caption += f"{escape(url)}"
    return caption


def callback_no_permission(existing_caption: str) -> str:
    return existing_caption + "\n\n😾 呜喵...你没有权限审核哦！"


def callback_not_found(existing_caption: str) -> str:
    return existing_caption + "\n\n😿 呜喵...投稿不存在哦！"


def callback_already_done(existing_caption: str, status: str) -> str:
    return existing_caption + f"\n\n✅ 已经被{status}啦喵！"


def callback_approved(existing_caption: str, username: str) -> str:
    return existing_caption + f"\n\n✅ 已经通过啦喵 by @{username}"


def callback_rejected(existing_caption: str, username: str) -> str:
    return existing_caption + f"\n\n❌ 已经被拒绝啦喵 by @{username}"


def callback_deleted(existing_caption: str, username: str) -> str:
    return existing_caption + f"\n\n🗑️ 已经删除啦喵 by @{username}"


def submitter_approved(url: str) -> str:
    return f"🥳 好消息喵！你的投稿已经通过啦：{url}"


def submitter_rejected(url: str) -> str:
    return f"😿 很遗憾喵...你的投稿被拒绝了：{url}"


def api_notify(submission_id: int, url: str, metadata: dict) -> str:
    notify_text = "📥 收到API投稿啦喵！\n\n"
    notify_text += f"投稿ID: #{submission_id}\n"
    notify_text += f"链接: {url}\n"
    if metadata.get("author_name"):
        notify_text += f"作者: {metadata.get('author_name')}\n"
    if metadata.get("title"):
        notify_text += f"标题: {metadata.get('title')}\n"
    notify_text += "已自动发布到频道 ✅"
    return notify_text


def moderation_caption(submission_id: int, url: str, metadata: dict) -> str:
    score = metadata.get("safety_score")
    score_text = f"{score:.2f}" if isinstance(score, (int, float)) else "n/a"
    class_text = metadata.get("safety_class") or "n/a"
    text = f"投稿 #{submission_id}\n"
    text += f"nudenet score {score_text}, {class_text}\n"
    text += f"{url}"
    return text


def admin_error(source: str, detail: str) -> str:
    return f"{ADMIN_ERROR_PREFIX}：{source}\n{detail}"
