from typing import Tuple, Dict, Union

from bilibili_api import Credential
from pydantic import BaseSettings, validator, EmailStr, SecretStr, AnyUrl


class Config(BaseSettings):
    # 全局设置
    fansub_senders: Tuple[str, ...] = ("",)
    fansub_masters: Tuple[str, ...] = ("",)
    fansub_groups: Tuple[int, ...] = (0,)  # 0号位群组用于debug时的推送，默认设置为0
    debug: bool = False

    # 时间设置中的单位均为分钟

    # Mail推送功能
    time_checkmailupdate: int = 5
    mail_recv_addr: EmailStr = ""
    mail_recv_pwd: SecretStr = ""
    pop3_server: str = "pop.qq.com"
    moni_addrs: Tuple[EmailStr, ...] = ("",)

    # 官方博客推送功能
    time_checkblogupdate: int = 10
    member_abbr: str = "haruka.kaki"

    # B站发送动态功能（部分字段请参考bilibili_api）
    time_waitbeforesend: int = 10
    time_waitforimages: int = 2
    dynamic_topic: str = "#贺喜遥香#"
    cred: Union[Credential, Dict[str, str]] = {"sessdata": "", "bili_jct": "", "buvid3": ""}

    # 官方推特推送功能（部分字段请参考Twitter API）
    time_checktwiupdate: int = 5
    twi_moni_keywords: Tuple[str, ...] = ["賀喜遥香", "君に叱られた"]
    twi_proxies: Dict[str, AnyUrl] = {"all://": "http://127.0.0.1:7890"}
    twi_params: Dict[str, str] = {"tweet.fields": "entities,created_at",
                                  "expansions": "attachments.media_keys",
                                  "media.fields": "preview_image_url,url,media_key,type,height",
                                  "max_results": "5",
                                  }
    twi_bearer_token: str = ""
    twi_headers: Dict[str, str] = {"Authorization": f"Bearer {twi_bearer_token}",
                                   "User-Agent": "v2UserTweetsPython",
                                   }

    @validator("cred")
    def cred_parser(cls, v):
        if v:
            if "sessdata" in v and "bili_jct" in v and "buvid3" in v:
                return Credential(**v)
            else:
                raise ValueError('must contain "sessdata", "bili_jct" and "buvid3"')

    class Config:
        extra = "ignore"
