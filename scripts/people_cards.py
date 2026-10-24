"""Render concise, sourced research cards from reviewed presenter insights."""
import argparse,json
from pathlib import Path


def card_fields(person):
    explicit=person.get('card',{})
    return {
        'research':explicit.get('research') or '；'.join(person['directions']),
        'progress':explicit.get('progress') or person['progress'],
        'open_question':explicit.get('open_question') or (person.get('follow_up') or ['待后续汇报补充'])[0]
    }


def card_lines(person,date,source):
    card=card_fields(person)
    return ['**在研究什么：** '+card['research'],'',
            '**最近进展：** '+card['progress'],'',
            '**当前问题：** '+card['open_question'],'',
            '**最近汇报：** '+date+'，'+person['title']+'。'+source]


def meeting_cards(event,link):
    lines=['# '+event['meeting_date']+' 组会人物名片','',
           '依据已整理的组会归纳当前研究内容，随新的汇报更新；完整技术讨论见对应报告。','']
    for person in event['presenters']:
        lines+=['## '+person['name'],'']+card_lines(person,event['meeting_date'],link(person))+['']
    return '\n'.join(lines).rstrip()+'\n'


def write_local(root):
    event=json.loads((root/'人物汇报提要.json').read_text('utf8'))
    output=root/'人物名片.md'
    text=meeting_cards(event,lambda p:'[完整报告](组会报告.md)')
    output.write_bytes(text.encode('utf8'))
    return output


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('meeting',type=Path)
    print(write_local(p.parse_args().meeting.resolve()))
