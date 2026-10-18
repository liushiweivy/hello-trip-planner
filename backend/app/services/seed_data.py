# -*- coding: utf-8 -*-
"""热门旅游城市 3 天预制路线（人工编写，零 token）。

- 只写大纲（风格 + 每天时间轴），详情按需生成后进运行时缓存
- 坐标一律不写死：serve 时用高德 POI 反查补全并回写（禁止编造坐标原则）
- date 留空，serve 时按请求日期 + day_index 重排
- 地址写到区级（真实地名），供高德反查与展示
"""

# 每城 4 套固定风格卡位：经典全景 / 深度慢游 / 经济实惠 / 自然风光
SEED_OUTLINES = {
    "北京": [
        {
            "plan_id": 1, "style": "经典全景", "tagline": "第一次来北京的必打卡环线",
            "highlights": ["故宫中轴线", "八达岭长城", "颐和园昆明湖"],
            "estimated_budget": 1600,
            "days": [
                {"day_index": 0, "title": "皇城根中轴线", "spots": [
                    {"name": "天安门广场", "address": "北京市东城区", "category": "地标"},
                    {"name": "故宫博物院", "address": "北京市东城区景山前街4号", "category": "古迹"},
                    {"name": "景山公园", "address": "北京市西城区景山西街44号", "category": "公园"}]},
                {"day_index": 1, "title": "长城一日", "spots": [
                    {"name": "八达岭长城", "address": "北京市延庆区", "category": "古迹"},
                    {"name": "奥林匹克公园", "address": "北京市朝阳区", "category": "地标"}]},
                {"day_index": 2, "title": "皇家园林", "spots": [
                    {"name": "颐和园", "address": "北京市海淀区新建宫门路19号", "category": "古迹"},
                    {"name": "圆明园遗址公园", "address": "北京市海淀区清华西路28号", "category": "古迹"},
                    {"name": "前门大街", "address": "北京市东城区", "category": "特色街区"}]},
            ],
        },
        {
            "plan_id": 2, "style": "深度慢游", "tagline": "一天一处，读懂北京",
            "highlights": ["故宫深度半日", "颐和园慢游", "胡同生活"],
            "estimated_budget": 1300,
            "days": [
                {"day_index": 0, "title": "故宫深度", "spots": [
                    {"name": "故宫博物院", "address": "北京市东城区景山前街4号", "category": "古迹"},
                    {"name": "景山公园", "address": "北京市西城区景山西街44号", "category": "公园"}]},
                {"day_index": 1, "title": "颐和园一日", "spots": [
                    {"name": "颐和园", "address": "北京市海淀区新建宫门路19号", "category": "古迹"}]},
                {"day_index": 2, "title": "胡同与湖", "spots": [
                    {"name": "南锣鼓巷", "address": "北京市东城区", "category": "特色街区"},
                    {"name": "什刹海", "address": "北京市西城区", "category": "湖泊"}]},
            ],
        },
        {
            "plan_id": 3, "style": "经济实惠", "tagline": "免费地标一样玩透",
            "highlights": ["天安门免费", "奥森免费", "胡同免费逛"],
            "estimated_budget": 500,
            "days": [
                {"day_index": 0, "title": "免费中轴线", "spots": [
                    {"name": "天安门广场", "address": "北京市东城区", "category": "地标"},
                    {"name": "景山公园", "address": "北京市西城区景山西街44号", "category": "公园"},
                    {"name": "前门大街", "address": "北京市东城区", "category": "特色街区"}]},
                {"day_index": 1, "title": "奥运记忆", "spots": [
                    {"name": "奥林匹克公园", "address": "北京市朝阳区", "category": "地标"},
                    {"name": "奥林匹克森林公园", "address": "北京市朝阳区", "category": "公园"}]},
                {"day_index": 2, "title": "胡同漫步", "spots": [
                    {"name": "南锣鼓巷", "address": "北京市东城区", "category": "特色街区"},
                    {"name": "什刹海", "address": "北京市西城区", "category": "湖泊"},
                    {"name": "烟袋斜街", "address": "北京市西城区", "category": "特色街区"}]},
            ],
        },
        {
            "plan_id": 4, "style": "自然风光", "tagline": "皇家山水与城市绿肺",
            "highlights": ["颐和园湖光", "香山红叶", "奥森氧吧"],
            "estimated_budget": 800,
            "days": [
                {"day_index": 0, "title": "湖光山色", "spots": [
                    {"name": "颐和园", "address": "北京市海淀区新建宫门路19号", "category": "湖泊"},
                    {"name": "圆明园遗址公园", "address": "北京市海淀区清华西路28号", "category": "公园"}]},
                {"day_index": 1, "title": "西山登高", "spots": [
                    {"name": "香山公园", "address": "北京市海淀区", "category": "山岳"},
                    {"name": "国家植物园", "address": "北京市海淀区香山", "category": "植物园"}]},
                {"day_index": 2, "title": "城市绿肺", "spots": [
                    {"name": "奥林匹克森林公园", "address": "北京市朝阳区", "category": "公园"},
                    {"name": "什刹海", "address": "北京市西城区", "category": "湖泊"}]},
            ],
        },
    ],
    "上海": [
        {
            "plan_id": 1, "style": "经典全景", "tagline": "外滩到陆家嘴的魔都名片",
            "highlights": ["外滩万国建筑", "东方明珠", "豫园城隍庙"],
            "estimated_budget": 1500,
            "days": [
                {"day_index": 0, "title": "浦西经典", "spots": [
                    {"name": "外滩", "address": "上海市黄浦区中山东一路", "category": "地标"},
                    {"name": "南京路步行街", "address": "上海市黄浦区", "category": "特色街区"},
                    {"name": "豫园", "address": "上海市黄浦区安仁街137号", "category": "古迹"}]},
                {"day_index": 1, "title": "陆家嘴天际线", "spots": [
                    {"name": "东方明珠广播电视塔", "address": "上海市浦东新区世纪大道1号", "category": "地标"},
                    {"name": "上海中心大厦观光厅", "address": "上海市浦东新区银城中路501号", "category": "地标"},
                    {"name": "陆家嘴中心绿地", "address": "上海市浦东新区", "category": "公园"}]},
                {"day_index": 2, "title": "文艺上海", "spots": [
                    {"name": "上海博物馆", "address": "上海市黄浦区人民大道201号", "category": "博物馆"},
                    {"name": "田子坊", "address": "上海市黄浦区泰康路210弄", "category": "特色街区"},
                    {"name": "新天地", "address": "上海市黄浦区", "category": "特色街区"}]},
            ],
        },
        {
            "plan_id": 2, "style": "深度慢游", "tagline": "梧桐区慢走，博物馆细品",
            "highlights": ["豫园品茗", "武康路洋房", "上博国宝"],
            "estimated_budget": 1200,
            "days": [
                {"day_index": 0, "title": "老城厢一日", "spots": [
                    {"name": "豫园", "address": "上海市黄浦区安仁街137号", "category": "古迹"},
                    {"name": "外滩", "address": "上海市黄浦区中山东一路", "category": "地标"}]},
                {"day_index": 1, "title": "博物馆与梧桐", "spots": [
                    {"name": "上海博物馆", "address": "上海市黄浦区人民大道201号", "category": "博物馆"},
                    {"name": "武康路历史文化名街", "address": "上海市徐汇区", "category": "特色街区"}]},
                {"day_index": 2, "title": "弄堂时光", "spots": [
                    {"name": "田子坊", "address": "上海市黄浦区泰康路210弄", "category": "特色街区"},
                    {"name": "思南公馆", "address": "上海市黄浦区思南路", "category": "特色街区"}]},
            ],
        },
        {
            "plan_id": 3, "style": "经济实惠", "tagline": "免费上海也很精彩",
            "highlights": ["外滩免费", "上博免费", "滨江步道"],
            "estimated_budget": 450,
            "days": [
                {"day_index": 0, "title": "免费经典", "spots": [
                    {"name": "外滩", "address": "上海市黄浦区中山东一路", "category": "地标"},
                    {"name": "南京路步行街", "address": "上海市黄浦区", "category": "特色街区"},
                    {"name": "人民公园", "address": "上海市黄浦区", "category": "公园"}]},
                {"day_index": 1, "title": "免费文化日", "spots": [
                    {"name": "上海博物馆", "address": "上海市黄浦区人民大道201号", "category": "博物馆"},
                    {"name": "武康路历史文化名街", "address": "上海市徐汇区", "category": "特色街区"}]},
                {"day_index": 2, "title": "免费滨江", "spots": [
                    {"name": "田子坊", "address": "上海市黄浦区泰康路210弄", "category": "特色街区"},
                    {"name": "滨江大道", "address": "上海市浦东新区", "category": "滨水步道"}]},
            ],
        },
        {
            "plan_id": 4, "style": "自然风光", "tagline": "魔都的绿与江",
            "highlights": ["世纪公园", "共青森林", "佘山登高"],
            "estimated_budget": 700,
            "days": [
                {"day_index": 0, "title": "城市绿心", "spots": [
                    {"name": "世纪公园", "address": "上海市浦东新区锦绣路1001号", "category": "公园"},
                    {"name": "滨江大道", "address": "上海市浦东新区", "category": "滨水步道"}]},
                {"day_index": 1, "title": "森林氧吧", "spots": [
                    {"name": "共青国家森林公园", "address": "上海市杨浦区军工路2000号", "category": "森林公园"}]},
                {"day_index": 2, "title": "佘山一日", "spots": [
                    {"name": "佘山国家森林公园", "address": "上海市松江区", "category": "山岳"},
                    {"name": "辰山植物园", "address": "上海市松江区辰花公路3888号", "category": "植物园"}]},
            ],
        },
    ],
    "西安": [
        {
            "plan_id": 1, "style": "经典全景", "tagline": "千年古都的标准答案",
            "highlights": ["兵马俑军阵", "明城墙骑行", "大唐不夜城"],
            "estimated_budget": 1500,
            "days": [
                {"day_index": 0, "title": "世界第八大奇迹", "spots": [
                    {"name": "秦始皇帝陵博物院(兵马俑)", "address": "西安市临潼区", "category": "博物馆"},
                    {"name": "华清宫", "address": "西安市临潼区华清路38号", "category": "古迹"}]},
                {"day_index": 1, "title": "古城墙内", "spots": [
                    {"name": "西安城墙", "address": "西安市碑林区", "category": "古迹"},
                    {"name": "西安钟楼", "address": "西安市碑林区", "category": "地标"},
                    {"name": "回民街", "address": "西安市莲湖区", "category": "美食街区"}]},
                {"day_index": 2, "title": "盛唐气象", "spots": [
                    {"name": "陕西历史博物馆", "address": "西安市雁塔区小寨东路91号", "category": "博物馆"},
                    {"name": "大雁塔", "address": "西安市雁塔区", "category": "古迹"},
                    {"name": "大唐不夜城", "address": "西安市雁塔区", "category": "特色街区"}]},
            ],
        },
        {
            "plan_id": 2, "style": "深度慢游", "tagline": "慢慢读懂十三朝",
            "highlights": ["兵马俑细品", "陕历博国宝", "碑林书法"],
            "estimated_budget": 1200,
            "days": [
                {"day_index": 0, "title": "帝陵一日", "spots": [
                    {"name": "秦始皇帝陵博物院(兵马俑)", "address": "西安市临潼区", "category": "博物馆"},
                    {"name": "秦始皇陵遗址公园(丽山园)", "address": "西安市临潼区", "category": "古迹"}]},
                {"day_index": 1, "title": "国宝与雁塔", "spots": [
                    {"name": "陕西历史博物馆", "address": "西安市雁塔区小寨东路91号", "category": "博物馆"},
                    {"name": "大雁塔", "address": "西安市雁塔区", "category": "古迹"}]},
                {"day_index": 2, "title": "碑林书香", "spots": [
                    {"name": "西安碑林博物馆", "address": "西安市碑林区三学街15号", "category": "博物馆"},
                    {"name": "书院门文化街", "address": "西安市碑林区", "category": "特色街区"},
                    {"name": "西安城墙", "address": "西安市碑林区", "category": "古迹"}]},
            ],
        },
        {
            "plan_id": 3, "style": "经济实惠", "tagline": "免费古都逛吃法",
            "highlights": ["城墙根免费", "不夜城免费", "陕历博免费"],
            "estimated_budget": 450,
            "days": [
                {"day_index": 0, "title": "城里免费逛", "spots": [
                    {"name": "西安钟楼", "address": "西安市碑林区", "category": "地标"},
                    {"name": "回民街", "address": "西安市莲湖区", "category": "美食街区"},
                    {"name": "书院门文化街", "address": "西安市碑林区", "category": "特色街区"}]},
                {"day_index": 1, "title": "雁塔免费夜", "spots": [
                    {"name": "大雁塔北广场", "address": "西安市雁塔区", "category": "地标"},
                    {"name": "大唐不夜城", "address": "西安市雁塔区", "category": "特色街区"}]},
                {"day_index": 2, "title": "免费博物馆", "spots": [
                    {"name": "陕西历史博物馆", "address": "西安市雁塔区小寨东路91号", "category": "博物馆"},
                    {"name": "小雁塔(西安博物院)", "address": "西安市碑林区友谊西路72号", "category": "博物馆"}]},
            ],
        },
        {
            "plan_id": 4, "style": "自然风光", "tagline": "骊山晚照与终南秀色",
            "highlights": ["骊山远眺", "翠华山天池", "昆明池"],
            "estimated_budget": 800,
            "days": [
                {"day_index": 0, "title": "骊山一日", "spots": [
                    {"name": "华清宫", "address": "西安市临潼区华清路38号", "category": "古迹"},
                    {"name": "骊山国家森林公园", "address": "西安市临潼区", "category": "山岳"}]},
                {"day_index": 1, "title": "终南翠华", "spots": [
                    {"name": "翠华山国家地质公园", "address": "西安市长安区", "category": "山岳"}]},
                {"day_index": 2, "title": "池湖风光", "spots": [
                    {"name": "昆明池·七夕公园", "address": "西安市长安区", "category": "湖泊"},
                    {"name": "汉城湖景区", "address": "西安市未央区", "category": "湖泊"}]},
            ],
        },
    ],
    "成都": [
        {
            "plan_id": 1, "style": "经典全景", "tagline": "熊猫古堰一座都不少",
            "highlights": ["大熊猫基地", "都江堰", "宽窄巷子"],
            "estimated_budget": 1400,
            "days": [
                {"day_index": 0, "title": "滚滚与巷子", "spots": [
                    {"name": "成都大熊猫繁育研究基地", "address": "成都市成华区熊猫大道1375号", "category": "动物园"},
                    {"name": "宽窄巷子", "address": "成都市青羊区", "category": "特色街区"}]},
                {"day_index": 1, "title": "三国与诗圣", "spots": [
                    {"name": "武侯祠", "address": "成都市武侯区武侯祠大街231号", "category": "古迹"},
                    {"name": "锦里古街", "address": "成都市武侯区", "category": "特色街区"},
                    {"name": "杜甫草堂", "address": "成都市青羊区青华路37号", "category": "古迹"}]},
                {"day_index": 2, "title": "青城都江堰", "spots": [
                    {"name": "都江堰景区", "address": "成都市都江堰市", "category": "古迹"},
                    {"name": "青城山", "address": "成都市都江堰市", "category": "山岳"}]},
            ],
        },
        {
            "plan_id": 2, "style": "深度慢游", "tagline": "泡茶馆摆龙门阵的慢成都",
            "highlights": ["熊猫产房细看", "草堂慢读", "灌县古城"],
            "estimated_budget": 1100,
            "days": [
                {"day_index": 0, "title": "熊猫与禅意", "spots": [
                    {"name": "成都大熊猫繁育研究基地", "address": "成都市成华区熊猫大道1375号", "category": "动物园"},
                    {"name": "文殊院", "address": "成都市青羊区文殊院街66号", "category": "寺庙"}]},
                {"day_index": 1, "title": "诗圣的园子", "spots": [
                    {"name": "杜甫草堂", "address": "成都市青羊区青华路37号", "category": "古迹"},
                    {"name": "浣花溪公园", "address": "成都市青羊区", "category": "公园"},
                    {"name": "宽窄巷子", "address": "成都市青羊区", "category": "特色街区"}]},
                {"day_index": 2, "title": "古堰慢游", "spots": [
                    {"name": "都江堰景区", "address": "成都市都江堰市", "category": "古迹"},
                    {"name": "灌县古城", "address": "成都市都江堰市", "category": "古镇"}]},
            ],
        },
        {
            "plan_id": 3, "style": "经济实惠", "tagline": "免费公园茶馆成都",
            "highlights": ["人民公园喝茶", "春熙路打望", "九眼桥夜景"],
            "estimated_budget": 400,
            "days": [
                {"day_index": 0, "title": "老成都慢生活", "spots": [
                    {"name": "人民公园", "address": "成都市青羊区少城路12号", "category": "公园"},
                    {"name": "宽窄巷子", "address": "成都市青羊区", "category": "特色街区"},
                    {"name": "锦里古街", "address": "成都市武侯区", "category": "特色街区"}]},
                {"day_index": 1, "title": "禅意与繁华", "spots": [
                    {"name": "文殊院", "address": "成都市青羊区文殊院街66号", "category": "寺庙"},
                    {"name": "春熙路", "address": "成都市锦江区", "category": "特色街区"},
                    {"name": "成都远洋太古里", "address": "成都市锦江区", "category": "特色街区"}]},
                {"day_index": 2, "title": "望江夜色", "spots": [
                    {"name": "望江楼公园", "address": "成都市武侯区望江路30号", "category": "公园"},
                    {"name": "九眼桥", "address": "成都市武侯区", "category": "地标"}]},
            ],
        },
        {
            "plan_id": 4, "style": "自然风光", "tagline": "青城天下幽",
            "highlights": ["青城山幽径", "都江堰岷江", "浣花溪"],
            "estimated_budget": 800,
            "days": [
                {"day_index": 0, "title": "熊猫与溪", "spots": [
                    {"name": "成都大熊猫繁育研究基地", "address": "成都市成华区熊猫大道1375号", "category": "动物园"},
                    {"name": "浣花溪公园", "address": "成都市青羊区", "category": "公园"}]},
                {"day_index": 1, "title": "青城问道", "spots": [
                    {"name": "青城山", "address": "成都市都江堰市", "category": "山岳"}]},
                {"day_index": 2, "title": "岷江古堰", "spots": [
                    {"name": "都江堰景区", "address": "成都市都江堰市", "category": "古迹"},
                    {"name": "离堆公园", "address": "成都市都江堰市", "category": "公园"}]},
            ],
        },
    ],
    "重庆": [
        {
            "plan_id": 1, "style": "经典全景", "tagline": "8D魔幻山城打卡线",
            "highlights": ["洪崖洞夜景", "长江索道", "磁器口"],
            "estimated_budget": 1300,
            "days": [
                {"day_index": 0, "title": "渝中半岛", "spots": [
                    {"name": "解放碑步行街", "address": "重庆市渝中区", "category": "地标"},
                    {"name": "长江索道", "address": "重庆市渝中区新华路151号", "category": "特色体验"},
                    {"name": "洪崖洞民俗风貌区", "address": "重庆市渝中区嘉陵江滨江路88号", "category": "地标"}]},
                {"day_index": 1, "title": "古镇与轻轨", "spots": [
                    {"name": "磁器口古镇", "address": "重庆市沙坪坝区", "category": "古镇"},
                    {"name": "李子坝轻轨站", "address": "重庆市渝中区", "category": "地标"},
                    {"name": "鹅岭二厂文创公园", "address": "重庆市渝中区鹅岭正街1号", "category": "文创园区"}]},
                {"day_index": 2, "title": "南山观城", "spots": [
                    {"name": "山城步道", "address": "重庆市渝中区", "category": "步道"},
                    {"name": "南山一棵树观景台", "address": "重庆市南岸区", "category": "观景台"},
                    {"name": "弹子石老街", "address": "重庆市南岸区", "category": "特色街区"}]},
            ],
        },
        {
            "plan_id": 2, "style": "深度慢游", "tagline": "爬坡上坎读山城",
            "highlights": ["洪崖洞深巷", "红色记忆", "山城步道"],
            "estimated_budget": 1000,
            "days": [
                {"day_index": 0, "title": "母城记忆", "spots": [
                    {"name": "解放碑步行街", "address": "重庆市渝中区", "category": "地标"},
                    {"name": "洪崖洞民俗风貌区", "address": "重庆市渝中区嘉陵江滨江路88号", "category": "地标"},
                    {"name": "千厮门大桥", "address": "重庆市渝中区", "category": "地标"}]},
                {"day_index": 1, "title": "古镇红岩", "spots": [
                    {"name": "磁器口古镇", "address": "重庆市沙坪坝区", "category": "古镇"},
                    {"name": "白公馆", "address": "重庆市沙坪坝区歌乐山", "category": "纪念馆"}]},
                {"day_index": 2, "title": "步道与夜景", "spots": [
                    {"name": "山城步道", "address": "重庆市渝中区", "category": "步道"},
                    {"name": "鹅岭公园", "address": "重庆市渝中区", "category": "公园"},
                    {"name": "南山一棵树观景台", "address": "重庆市南岸区", "category": "观景台"}]},
            ],
        },
        {
            "plan_id": 3, "style": "经济实惠", "tagline": "免费山城照样魔幻",
            "highlights": ["洪崖洞免费", "轻轨穿楼免费看", "步道免费"],
            "estimated_budget": 400,
            "days": [
                {"day_index": 0, "title": "免费魔幻夜", "spots": [
                    {"name": "解放碑步行街", "address": "重庆市渝中区", "category": "地标"},
                    {"name": "洪崖洞民俗风貌区", "address": "重庆市渝中区嘉陵江滨江路88号", "category": "地标"},
                    {"name": "千厮门大桥", "address": "重庆市渝中区", "category": "地标"}]},
                {"day_index": 1, "title": "网红免费线", "spots": [
                    {"name": "李子坝轻轨站", "address": "重庆市渝中区", "category": "地标"},
                    {"name": "鹅岭二厂文创公园", "address": "重庆市渝中区鹅岭正街1号", "category": "文创园区"},
                    {"name": "山城步道", "address": "重庆市渝中区", "category": "步道"}]},
                {"day_index": 2, "title": "古镇商圈", "spots": [
                    {"name": "磁器口古镇", "address": "重庆市沙坪坝区", "category": "古镇"},
                    {"name": "观音桥步行街", "address": "重庆市江北区", "category": "特色街区"}]},
            ],
        },
        {
            "plan_id": 4, "style": "自然风光", "tagline": "南山歌乐与武隆天坑",
            "highlights": ["南山植物园", "歌乐山森林", "天生三桥"],
            "estimated_budget": 1100,
            "days": [
                {"day_index": 0, "title": "南山一日", "spots": [
                    {"name": "南山植物园", "address": "重庆市南岸区", "category": "植物园"},
                    {"name": "南山一棵树观景台", "address": "重庆市南岸区", "category": "观景台"}]},
                {"day_index": 1, "title": "歌乐山氧吧", "spots": [
                    {"name": "歌乐山国家森林公园", "address": "重庆市沙坪坝区", "category": "森林公园"}]},
                {"day_index": 2, "title": "武隆奇观", "spots": [
                    {"name": "武隆天生三桥", "address": "重庆市武隆区", "category": "地质奇观"}]},
            ],
        },
    ],
    "杭州": [
        {
            "plan_id": 1, "style": "经典全景", "tagline": "西湖十景精华环线",
            "highlights": ["断桥残雪", "灵隐飞来峰", "西溪湿地"],
            "estimated_budget": 1300,
            "days": [
                {"day_index": 0, "title": "西湖经典", "spots": [
                    {"name": "断桥残雪", "address": "杭州市西湖区北山街", "category": "湖泊"},
                    {"name": "苏堤春晓", "address": "杭州市西湖区", "category": "湖泊"},
                    {"name": "雷峰塔", "address": "杭州市西湖区南山路15号", "category": "古迹"}]},
                {"day_index": 1, "title": "灵隐禅意", "spots": [
                    {"name": "灵隐寺", "address": "杭州市西湖区法云弄1号", "category": "寺庙"},
                    {"name": "飞来峰", "address": "杭州市西湖区灵隐路", "category": "山岳"},
                    {"name": "龙井村", "address": "杭州市西湖区", "category": "村落"}]},
                {"day_index": 2, "title": "湿地与古街", "spots": [
                    {"name": "西溪国家湿地公园", "address": "杭州市西湖区天目山路518号", "category": "湿地"},
                    {"name": "河坊街", "address": "杭州市上城区", "category": "特色街区"}]},
            ],
        },
        {
            "plan_id": 2, "style": "深度慢游", "tagline": "西湖西线慢慢走",
            "highlights": ["苏堤晨走", "三天竺问禅", "九溪烟树"],
            "estimated_budget": 1000,
            "days": [
                {"day_index": 0, "title": "西湖西线", "spots": [
                    {"name": "苏堤春晓", "address": "杭州市西湖区", "category": "湖泊"},
                    {"name": "花港观鱼", "address": "杭州市西湖区西山路5-1号", "category": "公园"}]},
                {"day_index": 1, "title": "天竺问茶", "spots": [
                    {"name": "灵隐寺", "address": "杭州市西湖区法云弄1号", "category": "寺庙"},
                    {"name": "龙井村", "address": "杭州市西湖区", "category": "村落"}]},
                {"day_index": 2, "title": "九溪听水", "spots": [
                    {"name": "九溪烟树", "address": "杭州市西湖区", "category": "溪谷"},
                    {"name": "六和塔", "address": "杭州市西湖区之江路16号", "category": "古迹"}]},
            ],
        },
        {
            "plan_id": 3, "style": "经济实惠", "tagline": "免费西湖玩三天",
            "highlights": ["环湖免费", "太子湾免费", "九溪免费"],
            "estimated_budget": 350,
            "days": [
                {"day_index": 0, "title": "环湖免费走", "spots": [
                    {"name": "断桥残雪", "address": "杭州市西湖区北山街", "category": "湖泊"},
                    {"name": "白堤", "address": "杭州市西湖区", "category": "湖泊"},
                    {"name": "苏堤春晓", "address": "杭州市西湖区", "category": "湖泊"}]},
                {"day_index": 1, "title": "花海与古街", "spots": [
                    {"name": "太子湾公园", "address": "杭州市西湖区南山路5-1号", "category": "公园"},
                    {"name": "河坊街", "address": "杭州市上城区", "category": "特色街区"},
                    {"name": "南宋御街", "address": "杭州市上城区", "category": "特色街区"}]},
                {"day_index": 2, "title": "九溪龙井", "spots": [
                    {"name": "九溪烟树", "address": "杭州市西湖区", "category": "溪谷"},
                    {"name": "龙井村", "address": "杭州市西湖区", "category": "村落"}]},
            ],
        },
        {
            "plan_id": 4, "style": "自然风光", "tagline": "山水杭州的绿野仙踪",
            "highlights": ["浴鹄湾秘境", "西溪湿地", "云栖竹径"],
            "estimated_budget": 700,
            "days": [
                {"day_index": 0, "title": "西湖秘境", "spots": [
                    {"name": "花港观鱼", "address": "杭州市西湖区西山路5-1号", "category": "公园"},
                    {"name": "浴鹄湾", "address": "杭州市西湖区三台山路", "category": "湖泊"}]},
                {"day_index": 1, "title": "湿地泛舟", "spots": [
                    {"name": "西溪国家湿地公园", "address": "杭州市西湖区天目山路518号", "category": "湿地"}]},
                {"day_index": 2, "title": "竹径溪谷", "spots": [
                    {"name": "云栖竹径", "address": "杭州市西湖区梅灵南路", "category": "竹林"},
                    {"name": "九溪十八涧", "address": "杭州市西湖区", "category": "溪谷"}]},
            ],
        },
    ],
    "厦门": [
        {
            "plan_id": 1, "style": "经典全景", "tagline": "鼓浪屿与环岛路标配",
            "highlights": ["鼓浪屿日光岩", "厦大芙蓉隧道", "曾厝垵"],
            "estimated_budget": 1300,
            "days": [
                {"day_index": 0, "title": "鼓浪屿一日", "spots": [
                    {"name": "日光岩", "address": "厦门市思明区鼓浪屿", "category": "山岳"},
                    {"name": "菽庄花园", "address": "厦门市思明区鼓浪屿港仔后路7号", "category": "园林"}]},
                {"day_index": 1, "title": "学府与海岸", "spots": [
                    {"name": "南普陀寺", "address": "厦门市思明区思明南路515号", "category": "寺庙"},
                    {"name": "厦门大学", "address": "厦门市思明区思明南路422号", "category": "校园"},
                    {"name": "环岛路", "address": "厦门市思明区", "category": "海滨"}]},
                {"day_index": 2, "title": "渔村与炮台", "spots": [
                    {"name": "曾厝垵", "address": "厦门市思明区", "category": "特色街区"},
                    {"name": "胡里山炮台", "address": "厦门市思明区曾厝垵路2号", "category": "古迹"},
                    {"name": "沙坡尾", "address": "厦门市思明区", "category": "特色街区"}]},
            ],
        },
        {
            "plan_id": 2, "style": "深度慢游", "tagline": "小岛慢生活与学村风情",
            "highlights": ["鼓浪屿小巷", "厦大慢逛", "集美学村"],
            "estimated_budget": 1100,
            "days": [
                {"day_index": 0, "title": "鼓浪屿深度", "spots": [
                    {"name": "日光岩", "address": "厦门市思明区鼓浪屿", "category": "山岳"},
                    {"name": "皓月园", "address": "厦门市思明区鼓浪屿", "category": "园林"},
                    {"name": "鼓浪屿龙头路商业街", "address": "厦门市思明区鼓浪屿", "category": "特色街区"}]},
                {"day_index": 1, "title": "南普陀与厦大", "spots": [
                    {"name": "南普陀寺", "address": "厦门市思明区思明南路515号", "category": "寺庙"},
                    {"name": "厦门大学", "address": "厦门市思明区思明南路422号", "category": "校园"},
                    {"name": "芙蓉隧道", "address": "厦门市思明区厦门大学内", "category": "涂鸦"}]},
                {"day_index": 2, "title": "集美学村", "spots": [
                    {"name": "集美学村", "address": "厦门市集美区", "category": "校园"},
                    {"name": "鳌园", "address": "厦门市集美区鳌园路24号", "category": "园林"}]},
            ],
        },
        {
            "plan_id": 3, "style": "经济实惠", "tagline": "海风免费吹三天",
            "highlights": ["环岛路骑行", "南普陀免费", "沙坡尾免费"],
            "estimated_budget": 450,
            "days": [
                {"day_index": 0, "title": "海岸线", "spots": [
                    {"name": "环岛路", "address": "厦门市思明区", "category": "海滨"},
                    {"name": "曾厝垵", "address": "厦门市思明区", "category": "特色街区"}]},
                {"day_index": 1, "title": "学府与文艺", "spots": [
                    {"name": "南普陀寺", "address": "厦门市思明区思明南路515号", "category": "寺庙"},
                    {"name": "沙坡尾", "address": "厦门市思明区", "category": "特色街区"},
                    {"name": "厦门大学", "address": "厦门市思明区思明南路422号", "category": "校园"}]},
                {"day_index": 2, "title": "鼓浪屿省票版", "spots": [
                    {"name": "鼓浪屿龙头路商业街", "address": "厦门市思明区鼓浪屿", "category": "特色街区"},
                    {"name": "港仔后海滨浴场", "address": "厦门市思明区鼓浪屿", "category": "海滨"}]},
            ],
        },
        {
            "plan_id": 4, "style": "自然风光", "tagline": "海岛与植物王国",
            "highlights": ["日光岩日出", "黄厝海滩", "万石植物园"],
            "estimated_budget": 700,
            "days": [
                {"day_index": 0, "title": "海岛自然", "spots": [
                    {"name": "日光岩", "address": "厦门市思明区鼓浪屿", "category": "山岳"},
                    {"name": "港仔后海滨浴场", "address": "厦门市思明区鼓浪屿", "category": "海滨"}]},
                {"day_index": 1, "title": "环岛海岸", "spots": [
                    {"name": "环岛路", "address": "厦门市思明区", "category": "海滨"},
                    {"name": "黄厝海滩", "address": "厦门市思明区", "category": "海滨"}]},
                {"day_index": 2, "title": "植物园", "spots": [
                    {"name": "厦门园林植物园", "address": "厦门市思明区虎园路25号", "category": "植物园"},
                    {"name": "铁路文化公园", "address": "厦门市思明区", "category": "公园"}]},
            ],
        },
    ],
    "广州": [
        {
            "plan_id": 1, "style": "经典全景", "tagline": "珠江新城到西关风情",
            "highlights": ["广州塔", "陈家祠", "沙面"],
            "estimated_budget": 1300,
            "days": [
                {"day_index": 0, "title": "新中轴线", "spots": [
                    {"name": "广州塔", "address": "广州市海珠区阅江西路222号", "category": "地标"},
                    {"name": "花城广场", "address": "广州市天河区", "category": "广场"},
                    {"name": "珠江夜游", "address": "广州市越秀区沿江路", "category": "特色体验"}]},
                {"day_index": 1, "title": "西关风情", "spots": [
                    {"name": "陈家祠", "address": "广州市荔湾区中山七路恩龙里34号", "category": "古迹"},
                    {"name": "沙面岛", "address": "广州市荔湾区", "category": "特色街区"},
                    {"name": "上下九步行街", "address": "广州市荔湾区", "category": "特色街区"}]},
                {"day_index": 2, "title": "羊城记忆", "spots": [
                    {"name": "越秀公园", "address": "广州市越秀区解放北路988号", "category": "公园"},
                    {"name": "北京路步行街", "address": "广州市越秀区", "category": "特色街区"},
                    {"name": "广东省博物馆", "address": "广州市天河区珠江东路2号", "category": "博物馆"}]},
            ],
        },
        {
            "plan_id": 2, "style": "深度慢游", "tagline": "读懂岭南的慢广州",
            "highlights": ["陈家祠细品", "西关大屋", "圣心大教堂"],
            "estimated_budget": 1000,
            "days": [
                {"day_index": 0, "title": "岭南匠心", "spots": [
                    {"name": "陈家祠", "address": "广州市荔湾区中山七路恩龙里34号", "category": "古迹"},
                    {"name": "荔湾湖公园", "address": "广州市荔湾区", "category": "公园"},
                    {"name": "荔枝湾涌", "address": "广州市荔湾区", "category": "水乡"}]},
                {"day_index": 1, "title": "欧陆遗风", "spots": [
                    {"name": "沙面岛", "address": "广州市荔湾区", "category": "特色街区"},
                    {"name": "石室圣心大教堂", "address": "广州市越秀区一德路56号", "category": "教堂"}]},
                {"day_index": 2, "title": "花城新貌", "spots": [
                    {"name": "广东省博物馆", "address": "广州市天河区珠江东路2号", "category": "博物馆"},
                    {"name": "花城广场", "address": "广州市天河区", "category": "广场"},
                    {"name": "广州塔", "address": "广州市海珠区阅江西路222号", "category": "地标"}]},
            ],
        },
        {
            "plan_id": 3, "style": "经济实惠", "tagline": "免费羊城吃到撑",
            "highlights": ["越秀公园免费", "沙面免费", "省博免费"],
            "estimated_budget": 400,
            "days": [
                {"day_index": 0, "title": "免费老城", "spots": [
                    {"name": "越秀公园", "address": "广州市越秀区解放北路988号", "category": "公园"},
                    {"name": "中山纪念堂", "address": "广州市越秀区东风中路299号", "category": "地标"},
                    {"name": "北京路步行街", "address": "广州市越秀区", "category": "特色街区"}]},
                {"day_index": 1, "title": "免费西关", "spots": [
                    {"name": "沙面岛", "address": "广州市荔湾区", "category": "特色街区"},
                    {"name": "上下九步行街", "address": "广州市荔湾区", "category": "特色街区"},
                    {"name": "荔枝湾涌", "address": "广州市荔湾区", "category": "水乡"}]},
                {"day_index": 2, "title": "免费新城", "spots": [
                    {"name": "花城广场", "address": "广州市天河区", "category": "广场"},
                    {"name": "广东省博物馆", "address": "广州市天河区珠江东路2号", "category": "博物馆"},
                    {"name": "海心沙亚运公园", "address": "广州市天河区", "category": "公园"}]},
            ],
        },
        {
            "plan_id": 4, "style": "自然风光", "tagline": "云山珠水湿地花城",
            "highlights": ["白云山摩星岭", "海珠湿地", "华南植物园"],
            "estimated_budget": 600,
            "days": [
                {"day_index": 0, "title": "白云登高", "spots": [
                    {"name": "白云山风景名胜区", "address": "广州市白云区", "category": "山岳"}]},
                {"day_index": 1, "title": "城央湿地", "spots": [
                    {"name": "海珠国家湿地公园", "address": "广州市海珠区", "category": "湿地"}]},
                {"day_index": 2, "title": "植物王国", "spots": [
                    {"name": "华南国家植物园", "address": "广州市天河区天源路1190号", "category": "植物园"}]},
            ],
        },
    ],
    "南京": [
        {
            "plan_id": 1, "style": "经典全景", "tagline": "六朝古都标配三天",
            "highlights": ["中山陵", "夫子庙秦淮河", "南京博物院"],
            "estimated_budget": 1200,
            "days": [
                {"day_index": 0, "title": "钟山陵寝", "spots": [
                    {"name": "中山陵", "address": "南京市玄武区石象路7号", "category": "陵墓"},
                    {"name": "明孝陵", "address": "南京市玄武区", "category": "陵墓"},
                    {"name": "美龄宫", "address": "南京市玄武区中山陵9号", "category": "古迹"}]},
                {"day_index": 1, "title": "民国与博物", "spots": [
                    {"name": "南京总统府", "address": "南京市玄武区长江路292号", "category": "古迹"},
                    {"name": "南京博物院", "address": "南京市玄武区中山东路321号", "category": "博物馆"},
                    {"name": "夫子庙秦淮风光带", "address": "南京市秦淮区", "category": "特色街区"}]},
                {"day_index": 2, "title": "湖寺城墙", "spots": [
                    {"name": "玄武湖公园", "address": "南京市玄武区", "category": "湖泊"},
                    {"name": "古鸡鸣寺", "address": "南京市玄武区鸡鸣寺路1号", "category": "寺庙"},
                    {"name": "南京城墙(台城段)", "address": "南京市玄武区", "category": "古迹"}]},
            ],
        },
        {
            "plan_id": 2, "style": "深度慢游", "tagline": "慢慢读一座金陵城",
            "highlights": ["石象路神道", "南博镇馆", "颐和路梧桐"],
            "estimated_budget": 1000,
            "days": [
                {"day_index": 0, "title": "钟山深度", "spots": [
                    {"name": "中山陵", "address": "南京市玄武区石象路7号", "category": "陵墓"},
                    {"name": "明孝陵", "address": "南京市玄武区", "category": "陵墓"}]},
                {"day_index": 1, "title": "博物与总统府", "spots": [
                    {"name": "南京博物院", "address": "南京市玄武区中山东路321号", "category": "博物馆"},
                    {"name": "南京总统府", "address": "南京市玄武区长江路292号", "category": "古迹"}]},
                {"day_index": 2, "title": "梧桐与湖", "spots": [
                    {"name": "颐和路公馆区", "address": "南京市鼓楼区", "category": "特色街区"},
                    {"name": "古鸡鸣寺", "address": "南京市玄武区鸡鸣寺路1号", "category": "寺庙"},
                    {"name": "玄武湖公园", "address": "南京市玄武区", "category": "湖泊"}]},
            ],
        },
        {
            "plan_id": 3, "style": "经济实惠", "tagline": "免费金陵逛个够",
            "highlights": ["玄武湖免费", "南博免费", "中山陵免费"],
            "estimated_budget": 350,
            "days": [
                {"day_index": 0, "title": "湖畔免费", "spots": [
                    {"name": "玄武湖公园", "address": "南京市玄武区", "category": "湖泊"},
                    {"name": "古鸡鸣寺", "address": "南京市玄武区鸡鸣寺路1号", "category": "寺庙"},
                    {"name": "南京城墙(台城段)", "address": "南京市玄武区", "category": "古迹"}]},
                {"day_index": 1, "title": "博物与夜市", "spots": [
                    {"name": "南京博物院", "address": "南京市玄武区中山东路321号", "category": "博物馆"},
                    {"name": "夫子庙秦淮风光带", "address": "南京市秦淮区", "category": "特色街区"},
                    {"name": "老门东历史街区", "address": "南京市秦淮区", "category": "特色街区"}]},
                {"day_index": 2, "title": "免费钟山", "spots": [
                    {"name": "中山陵", "address": "南京市玄武区石象路7号", "category": "陵墓"},
                    {"name": "紫金山绿道", "address": "南京市玄武区", "category": "步道"}]},
            ],
        },
        {
            "plan_id": 4, "style": "自然风光", "tagline": "紫金玄武的山水城林",
            "highlights": ["紫金山流徽榭", "玄武湖五洲", "莫愁湖"],
            "estimated_budget": 500,
            "days": [
                {"day_index": 0, "title": "紫金山野", "spots": [
                    {"name": "中山陵", "address": "南京市玄武区石象路7号", "category": "陵墓"},
                    {"name": "流徽榭", "address": "南京市玄武区钟山风景区内", "category": "湖泊"}]},
                {"day_index": 1, "title": "玄武湖洲", "spots": [
                    {"name": "玄武湖公园", "address": "南京市玄武区", "category": "湖泊"},
                    {"name": "情侣园", "address": "南京市玄武区", "category": "公园"}]},
                {"day_index": 2, "title": "莫愁绿博", "spots": [
                    {"name": "莫愁湖公园", "address": "南京市建邺区水西门大街132号", "category": "湖泊"},
                    {"name": "绿博园", "address": "南京市建邺区扬子江大道228号", "category": "公园"}]},
            ],
        },
    ],
    "青岛": [
        {
            "plan_id": 1, "style": "经典全景", "tagline": "红瓦绿树碧海蓝天",
            "highlights": ["栈桥回澜阁", "八大关", "崂山"],
            "estimated_budget": 1300,
            "days": [
                {"day_index": 0, "title": "老城海岸", "spots": [
                    {"name": "栈桥", "address": "青岛市市南区太平路12号", "category": "地标"},
                    {"name": "八大关风景区", "address": "青岛市市南区", "category": "特色街区"},
                    {"name": "五四广场", "address": "青岛市市南区", "category": "地标"}]},
                {"day_index": 1, "title": "海上仙山", "spots": [
                    {"name": "崂山仰口风景区", "address": "青岛市崂山区", "category": "山岳"}]},
                {"day_index": 2, "title": "啤酒与浴场", "spots": [
                    {"name": "青岛啤酒博物馆", "address": "青岛市市北区登州路56号", "category": "博物馆"},
                    {"name": "小鱼山公园", "address": "青岛市市南区福山支路24号", "category": "公园"},
                    {"name": "第一海水浴场", "address": "青岛市市南区", "category": "海滨"}]},
            ],
        },
        {
            "plan_id": 2, "style": "深度慢游", "tagline": "老城慢慢走海慢慢看",
            "highlights": ["天主教堂", "大学路咖啡", "太清道观"],
            "estimated_budget": 1000,
            "days": [
                {"day_index": 0, "title": "老城漫步", "spots": [
                    {"name": "栈桥", "address": "青岛市市南区太平路12号", "category": "地标"},
                    {"name": "浙江路天主教堂", "address": "青岛市市南区浙江路15号", "category": "教堂"},
                    {"name": "大学路", "address": "青岛市市南区", "category": "特色街区"}]},
                {"day_index": 1, "title": "八大关深度", "spots": [
                    {"name": "八大关风景区", "address": "青岛市市南区", "category": "特色街区"},
                    {"name": "第二海水浴场", "address": "青岛市市南区", "category": "海滨"},
                    {"name": "小鱼山公园", "address": "青岛市市南区福山支路24号", "category": "公园"}]},
                {"day_index": 2, "title": "崂山太清", "spots": [
                    {"name": "崂山太清宫", "address": "青岛市崂山区", "category": "道观"}]},
            ],
        },
        {
            "plan_id": 3, "style": "经济实惠", "tagline": "免费海滨玩三天",
            "highlights": ["栈桥免费", "八大关免费", "小麦岛免费"],
            "estimated_budget": 400,
            "days": [
                {"day_index": 0, "title": "免费老城", "spots": [
                    {"name": "栈桥", "address": "青岛市市南区太平路12号", "category": "地标"},
                    {"name": "中山路", "address": "青岛市市南区", "category": "特色街区"},
                    {"name": "劈柴院", "address": "青岛市市南区", "category": "美食街区"}]},
                {"day_index": 1, "title": "免费海岸", "spots": [
                    {"name": "八大关风景区", "address": "青岛市市南区", "category": "特色街区"},
                    {"name": "五四广场", "address": "青岛市市南区", "category": "地标"},
                    {"name": "奥帆中心", "address": "青岛市市南区", "category": "地标"}]},
                {"day_index": 2, "title": "免费海岛", "spots": [
                    {"name": "小麦岛公园", "address": "青岛市崂山区", "category": "公园"},
                    {"name": "石老人海水浴场", "address": "青岛市崂山区", "category": "海滨"}]},
            ],
        },
        {
            "plan_id": 4, "style": "自然风光", "tagline": "山海相连的黄金海岸",
            "highlights": ["崂山山海", "石老人日出", "金沙滩"],
            "estimated_budget": 800,
            "days": [
                {"day_index": 0, "title": "崂山山海", "spots": [
                    {"name": "崂山仰口风景区", "address": "青岛市崂山区", "category": "山岳"},
                    {"name": "崂山太清宫", "address": "青岛市崂山区", "category": "道观"}]},
                {"day_index": 1, "title": "东海岸", "spots": [
                    {"name": "小麦岛公园", "address": "青岛市崂山区", "category": "公园"},
                    {"name": "石老人海水浴场", "address": "青岛市崂山区", "category": "海滨"}]},
                {"day_index": 2, "title": "西海岸", "spots": [
                    {"name": "金沙滩", "address": "青岛市黄岛区", "category": "海滨"},
                    {"name": "唐岛湾滨海公园", "address": "青岛市黄岛区", "category": "公园"}]},
            ],
        },
    ],
}
