"""Synthetic QA fixtures only. Never invoked by installation or normal startup."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from ats_portal.web import create_app
from ats_portal.config import Settings


def seed(settings):
    with TestClient(create_app(settings)) as client:
        headers = {'Authorization': 'Bearer ' + settings.upload_token}
        for index in range(8):
            mode = ['stress', 'video_size_traverse', 'fixture-custom-mode', 'aging'][index % 4]
            label = {'stress': '综合压测', 'video_size_traverse': '录像尺寸遍历', 'fixture-custom-mode': '自定义扩展场景', 'aging': '长时间稳定性测试'}[mode]
            issue = index % 3 != 0
            results = []
            for i in range(12+index*3):
                results.append({'name': f'{["photo[auto]", "video[3k]", "rtmp"][i%3]}', 'module': ['photo','video','rtmp'][i%3], 'status': 'FAIL' if issue and i==5 and index%2 else 'PASS', 'cycle': i//3+1, 'rep': 1, 'message': '未收到录像完成信号' if issue and i==5 and index%2 else '执行完成', 'detail': 'TT ERROR 命中 1 次：\n第1次: TT ERROR camera' if issue and i==1 else '', 'elapsed_ms': 3000})
            report = {'results': results, 'recovery_events': []}
            logs = '[10:42:00.000] RX< camera initialized\n[10:42:02.121] RX< TT ERROR camera\n[10:42:02.150] RX< <script>window.__xss=true</script>\n' + ''.join(f'[10:42:{i%60:02}.100] RX< f_index:{i} capture progress\n' for i in range(1000))
            files = {'result.json': json.dumps(report, ensure_ascii=False).encode(), 'serial.log': logs.encode(), 'run.log': b'[INFO] SYNTHETIC QA FIXTURE\n[PASS] video[3k]\n'}
            started = datetime(2026,9,28,10,40,tzinfo=timezone(timedelta(hours=8)))-timedelta(hours=index*3)
            manifest = {'schema_version': 1, 'run_id': f'qa-demo-20260928-{index+1:03}', 'mode': {'id': mode,'name': label}, 'started_at': started.isoformat(), 'ended_at': (started+timedelta(hours=2,minutes=12)).isoformat(), 'execution_state': 'completed', 'source': {'host':'ubuntu-workstation','device':'EVB-DEMO','firmware':'示例数据'}, 'artifacts':[{'name':name,'kind':'report' if name.endswith('.json') else 'log','size':len(data),'sha256':hashlib.sha256(data).hexdigest()} for name,data in files.items()]}
            r=client.post('/api/v1/runs',json=manifest,headers=headers)
            assert r.status_code in (200,201),r.text
            for name,data in files.items():
                assert client.put(f'/api/v1/runs/{manifest["run_id"]}/artifacts/{name}',content=data,headers=headers).status_code==200
            r=client.post(f'/api/v1/runs/{manifest["run_id"]}/finalize',headers=headers)
            assert r.status_code==200,r.text


if __name__=='__main__':
    cfg=Settings.from_env()
    cfg.allowed_hosts=(*cfg.allowed_hosts,'testserver')
    seed(cfg)
    print('Inserted 8 explicitly synthetic QA runs into',cfg.data_dir)
