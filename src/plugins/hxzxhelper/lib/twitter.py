import nonebot
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Tuple, Dict
from dateutil.parser import parser
from nonebot.log import logger
from pydantic import BaseModel

from .utils import get_advanced
from ..config import Config
from ..model import ParsedObject

global_config = nonebot.get_driver().config
plugin_config = Config(**global_config.dict())
time_parser = parser()

RECENT_TWEET_URL = "https://api.twitter.com/2/tweets/search/recent"
GET_TWEET_URL = "https://api.twitter.com/2/tweets"

newest_twi_id = ""
_last_newest_twi_id = ""


class Attachment(BaseModel):
    media_keys: List[str]


class Urls(BaseModel):
    start: int
    end: int
    url: str
    expanded_url: str
    display_url: str


class Entities(BaseModel):
    urls: Optional[List[Urls]]


class ReferencedTweets(BaseModel):
    type: str  # retweeted转推：转发且不评论 quote引用：转发且评论 replied_to：评论
    id: str


class TweetData(BaseModel):
    entities: Entities
    text: str
    created_at: datetime    # "created_at": "2021-11-06T13:27:30.000Z"
    id: str
    author_id: str
    attachments: Optional[Attachment]
    referenced_tweets: Optional[List[ReferencedTweets]]


class TweetMedia(BaseModel):
    media_key: str
    type: str
    url: Optional[str]
    preview_image_url: Optional[str]


class PublicMetrics(BaseModel):
    followers_count: int
    following_count: int
    tweet_count: int
    listed_count: int


class TweetUser(BaseModel):
    id: str
    public_metrics: PublicMetrics
    name: str
    username: str


class TweetInclude(BaseModel):
    media: Optional[List[TweetMedia]]
    users: List[TweetUser]


class TweetMeta(BaseModel):
    newest_id: Optional[str]
    oldest_id: Optional[str]
    result_count: int
    next_token: Optional[str]


class TweetAPI(BaseModel):
    data: Optional[List[TweetData]]
    includes: Optional[TweetInclude]
    meta: Optional[TweetMeta]


async def _download_latest_tweet(topic_keyword: str, update=False) -> TweetAPI:
    params = {"query": f"#{topic_keyword} is:verified lang:ja",  # -is:retweet -is:reply -is:quote
              "tweet.fields": "entities,created_at,referenced_tweets",
              "user.fields": "name,public_metrics",
              "expansions": "author_id,attachments.media_keys",
              "media.fields": "url,media_key,type,preview_image_url",
              "max_results": 30,
              }
    if newest_twi_id and update:
        params.update({"since_id": newest_twi_id})
    ret = await get_advanced("https://api.twitter.com/2/tweets/search/recent",
                             params=params, proxies=plugin_config.proxies, headers=plugin_config.tweet_headers)
    if ret:
        return TweetAPI.parse_raw(ret.text)
    else:
        raise ValueError("下载到的推文内容为空")


async def get_refer_tweet(tweet_id: str) -> TweetAPI:
    params = {"ids": tweet_id,
              "tweet.fields": "entities,created_at",
              "user.fields": "name,public_metrics",
              "expansions": "author_id,attachments.media_keys",
              "media.fields": "url,media_key,type,preview_image_url",
              }
    response = await get_advanced(GET_TWEET_URL, headers=plugin_config.tweet_headers,
                                  params=params, proxies=plugin_config.proxies)
    return TweetAPI.parse_raw(response.text)


def remove_urls_in_tweet(tweet: TweetData) -> TweetData:
    """
        将pic.twitter.com和dlvr.it的链接直接删除，将其他链接替换为显示链接
    :param tweet:
    :return:
    """
    if tweet.entities.urls:
        for url in tweet.entities.urls:
            if url.display_url.find("pic.twitter.com") == -1 and url.display_url.find("dlvr.it") == -1:
                tweet.text = tweet.text.replace(url.url, url.expanded_url)
            else:
                tweet.text = tweet.text.replace(url.url, "")
    return tweet


async def parse_tweet(t: TweetAPI) -> Tuple[str, List[ParsedObject]]:
    """
    对从TwitterAPI收到的json返回值进行处理，返回 最新推文id 和 提取后的推文文字&图像

    *会递归下载提及到的推特

    :param t: TwitterAPI原始返回值
    :return: 当没有发现推文时返回空tuple，否则返回最新推文id和Tuple[ParsedTweet]
    """
    if not t.meta.result_count:
        return "", []

    twi_ids = []
    twi_users = []
    twi_images: Dict[str, TweetMedia] = {}
    twi_data: Dict[str, List[TweetData]] = {}
    twi_refer = []

    if t.includes.media:
        for media in t.includes.media:
            twi_images[media.media_key] = media
    for tweet in t.data:
        twi_ids.append(tweet.id)
        if tweet.referenced_tweets:
            ref_tweet = tweet.referenced_tweets[0]
            if ref_tweet.id not in twi_refer:
                twi_refer.append(ref_tweet.id)
        if tweet.author_id in twi_data:
            twi_data[tweet.author_id].append(tweet)
        else:
            twi_data[tweet.author_id] = [tweet, ]
            twi_users.append(tweet.author_id)

    if twi_refer:
        tt = await get_refer_tweet(",".join(twi_refer))
        if tt.includes.media:
            for media in tt.includes.media:
                if media.media_key not in twi_images:
                    twi_images[media.media_key] = media
        for tweet in tt.data:
            if tweet.id not in twi_ids:
                twi_ids.append(tweet.id)
                if tweet.author_id in twi_data:
                    twi_data[tweet.author_id].append(tweet)
                else:
                    twi_data[tweet.author_id] = [tweet, ]
        t.includes.users += [user for user in tt.includes.users if user.id not in twi_users]

    msgs = []
    for user in t.includes.users:
        for tweet in twi_data[user.id]:
            if tweet.referenced_tweets:
                if tweet.referenced_tweets[0].type == "retweeted":
                    continue
            if tweet.entities:
                tweet = remove_urls_in_tweet(tweet)
            cst = timezone(timedelta(hours=8))
            tweet_time = tweet.created_at.astimezone(cst).replace(microsecond=0)
            text = f"时间：{tweet_time.year}年{tweet_time.month}月{tweet_time.day}日 {tweet_time.time()}\n" \
                   f"【推特更新】\n\n" \
                   f"{user.name} @{user.username}:"
            text += tweet.text
            urls = []
            if tweet.attachments:
                for media_key in tweet.attachments.media_keys:
                    if twi_images[media_key].url:
                        url = twi_images[media_key].url
                    else:
                        url = twi_images[media_key].preview_image_url
                    urls.append(url)
            po = ParsedObject(text=text, images_url=urls, timestamp=str(int(tweet_time.timestamp())))
            msgs.append(po)
            logger.debug(f"处理过的的推文：{po}")

    return t.meta.newest_id, msgs


async def check_tweet_update() -> List[ParsedObject]:
    try:
        tweets = []
        for keyword in plugin_config.tweet_moni_keywords:
            tweet_json = await _download_latest_tweet(topic_keyword=keyword, update=True)
            _newest_twi_id, _tweets = await parse_tweet(tweet_json)
            global newest_twi_id, _last_newest_twi_id
            if _newest_twi_id > newest_twi_id:
                logger.warning(f"发现推特更新，匹配关键词<{keyword}>")
                _last_newest_twi_id = newest_twi_id
                newest_twi_id = _newest_twi_id
            tweets += _tweets

        return tweets
    except ValueError as errmsg:
        logger.error(f"自动获取最新推文失败：{errmsg}")
        return []


async def get_tweets_f() -> List[ParsedObject]:
    try:
        tweets = []
        for keyword in plugin_config.tweet_moni_keywords:
            tweet_json = await _download_latest_tweet(topic_keyword=keyword, update=False)
            _newest_twi_id, _tweets = await parse_tweet(tweet_json)
            global newest_twi_id, _last_newest_twi_id
            if _newest_twi_id > newest_twi_id:
                _last_newest_twi_id = newest_twi_id
                newest_twi_id = _newest_twi_id
            tweets += _tweets

        return tweets
    except ValueError as errmsg:
        logger.error(f"手动获取最新推文失败：{errmsg}")
        raise ValueError(errmsg)


async def tweet_initial():
    await get_tweets_f()


async def restore_tweet_id():
    """
    将newest_twi_id恢复到上一次的值。

    用于推特中的图片在PO转Message阶段下载失败时，判定本次获取推特更新失败。
    """
    global newest_twi_id, _last_newest_twi_id
    newest_twi_id = _last_newest_twi_id
