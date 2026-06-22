"""
어쩌다 월드컵 배팅 🏆
한국 vs 남아공 직원 배팅 앱
"""
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import sqlite3
import os

app = Flask(__name__)
app.secret_key = 'korea_vs_rsa_worldcup_2026'

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Railway Volume 사용 시 /data, 로컬은 현재 폴더
_db_dir = os.environ.get('DB_DIR', BASE_DIR)
DATABASE = os.path.join(_db_dir, 'bets.db')
ADMIN_PWD = os.environ.get('ADMIN_PWD', 'admin2026')
TOTAL_COINS = 1000
PORT = int(os.environ.get('PORT', 5001))


CATEGORIES = {
    'result': {
        'name': '⚽ 경기 최종 결과',
        'desc': '90분 풀타임 기준 (연장 제외)',
        'required': True,
        'min_coins': 200,
        'options': [
            {'value': 'korea_win', 'label': '🇰🇷 한국 승', 'odds': 2.5},
            {'value': 'draw',      'label': '🤝 무승부',   'odds': 3.5},
            {'value': 'rsa_win',   'label': '🇿🇦 남아공 승', 'odds': 5.0},
        ]
    },
    'score': {
        'name': '🎯 정확한 스코어',
        'desc': '최종 스코어 딱 맞추면 15배!',
        'required': False,
        'min_coins': 0,
        'options': 'score_input',
        'odds': 15.0
    },
    'goal_diff': {
        'name': '📊 골 득실차',
        'desc': '몇 골 차이로 경기가 끝날까?',
        'required': False,
        'min_coins': 0,
        'options': [
            {'value': '0',  'label': '0골 (동점)',     'odds': 2.0},
            {'value': '1',  'label': '1골 차이',       'odds': 2.0},
            {'value': '2',  'label': '2골 차이',       'odds': 3.0},
            {'value': '3+', 'label': '3골 이상 차이',  'odds': 5.0},
        ]
    },
    'first_goal': {
        'name': '🚀 선취득점 팀',
        'desc': '먼저 골을 넣는 팀은 어디?',
        'required': False,
        'min_coins': 0,
        'options': [
            {'value': 'korea', 'label': '🇰🇷 한국 먼저', 'odds': 2.0},
            {'value': 'rsa',   'label': '🇿🇦 남아공 먼저', 'odds': 3.0},
            {'value': 'none',  'label': '🙅 없음 (0-0)', 'odds': 5.0},
        ]
    },
    'total_goals': {
        'name': '💥 총 득점 수',
        'desc': '양팀 합산 총 골 수는?',
        'required': False,
        'min_coins': 0,
        'options': [
            {'value': '0-1', 'label': '0~1골',   'odds': 3.5},
            {'value': '2-3', 'label': '2~3골',   'odds': 2.0},
            {'value': '4-5', 'label': '4~5골',   'odds': 3.0},
            {'value': '6+',  'label': '6골 이상', 'odds': 6.0},
        ]
    },
}

# 스페셜 이벤트는 각각 독립적인 yes/no 배팅
SPECIAL_EVENTS = [
    {'value': 'penalty',   'label': '🥅 페널티킥 발생',  'odds': 3.0},
    {'value': 'red_card',  'label': '🟥 퇴장 발생',      'odds': 4.0},
    {'value': 'comeback',  'label': '🔄 역전극 발생',    'odds': 5.0},
    {'value': 'extra_time','label': '⏱️ 연장전 돌입',   'odds': 4.0},
]


# ── DB ──────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            department TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            participant_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            choice TEXT NOT NULL,
            coins INTEGER NOT NULL,
            odds REAL NOT NULL,
            FOREIGN KEY (participant_id) REFERENCES participants(id)
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        INSERT OR IGNORE INTO settings VALUES ('betting_open','1');
        INSERT OR IGNORE INTO settings VALUES ('result_set','0');
        INSERT OR IGNORE INTO settings VALUES ('korea_score',NULL);
        INSERT OR IGNORE INTO settings VALUES ('rsa_score',NULL);
        INSERT OR IGNORE INTO settings VALUES ('first_goal',NULL);
        INSERT OR IGNORE INTO settings VALUES ('penalty','0');
        INSERT OR IGNORE INTO settings VALUES ('red_card','0');
        INSERT OR IGNORE INTO settings VALUES ('comeback','0');
        INSERT OR IGNORE INTO settings VALUES ('extra_time','0');
        ''')
        db.commit()


def get_setting(key):
    with get_db() as db:
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row['value'] if row else None


def set_setting(key, value):
    with get_db() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, str(value) if value is not None else None))
        db.commit()


# ── 결과 계산 ────────────────────────────────────────

def calculate_results():
    k = int(get_setting('korea_score') or 0)
    r = int(get_setting('rsa_score') or 0)
    first_goal = get_setting('first_goal') or 'none'
    special_truth = {
        'penalty':    get_setting('penalty') == '1',
        'red_card':   get_setting('red_card') == '1',
        'comeback':   get_setting('comeback') == '1',
        'extra_time': get_setting('extra_time') == '1',
    }

    # 정답 계산
    correct = {}
    correct['result'] = 'korea_win' if k > r else ('draw' if k == r else 'rsa_win')
    correct['score'] = f'{k}-{r}'
    diff = abs(k - r)
    correct['goal_diff'] = '0' if diff == 0 else ('1' if diff == 1 else ('2' if diff == 2 else '3+'))
    correct['first_goal'] = first_goal
    total = k + r
    correct['total_goals'] = '0-1' if total <= 1 else ('2-3' if total <= 3 else ('4-5' if total <= 5 else '6+'))

    with get_db() as db:
        participants = db.execute('SELECT * FROM participants ORDER BY created_at').fetchall()
        results = []
        for p in participants:
            bets = db.execute('SELECT * FROM bets WHERE participant_id=?', (p['id'],)).fetchall()
            total_pts = 0
            bet_rows = []
            for b in bets:
                cat, choice, coins, odds = b['category'], b['choice'], b['coins'], b['odds']
                if cat == 'special':
                    won = special_truth.get(choice, False)
                else:
                    won = correct.get(cat) == choice
                pts = int(coins * odds) if won else 0
                total_pts += pts
                bet_rows.append({'category': cat, 'choice': choice,
                                 'coins': coins, 'odds': odds, 'won': won, 'pts': pts})
            results.append({
                'id': p['id'], 'name': p['name'], 'department': p['department'],
                'total_pts': total_pts, 'bets': bet_rows, 'prize': 0
            })

    results.sort(key=lambda x: x['total_pts'], reverse=True)
    n = len(results)
    pool = n * 10000
    dist = [0.5, 0.3, 0.2] if n >= 3 else ([0.7, 0.3] if n == 2 else [1.0])
    for i, res in enumerate(results):
        res['prize'] = int(pool * dist[i]) if i < len(dist) else 0

    return {
        'korea_score': k, 'rsa_score': r,
        'correct': correct, 'special_truth': special_truth,
        'participants': results, 'pool': pool
    }


# ── 라우트 ───────────────────────────────────────────

@app.route('/')
def index():
    betting_open = get_setting('betting_open') == '1'
    result_set = get_setting('result_set') == '1'
    with get_db() as db:
        count = db.execute('SELECT COUNT(*) as c FROM participants').fetchone()['c']

    participant = None
    if 'pid' in session:
        with get_db() as db:
            participant = db.execute('SELECT * FROM participants WHERE id=?', (session['pid'],)).fetchone()

    return render_template('index.html',
                           betting_open=betting_open,
                           result_set=result_set,
                           count=count,
                           pool=count * 10000,
                           participant=participant)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        dept = request.form.get('department', '').strip()
        if not name or not dept:
            flash('이름과 부서를 모두 입력해주세요.', 'error')
            return redirect(url_for('register'))
        try:
            with get_db() as db:
                cur = db.execute('INSERT INTO participants (name, department) VALUES (?,?)', (name, dept))
                db.commit()
                session['pid'] = cur.lastrowid
                session['pname'] = name
            flash(f'환영합니다, {name}님! 🎉', 'success')
            return redirect(url_for('bet'))
        except sqlite3.IntegrityError:
            with get_db() as db:
                p = db.execute('SELECT * FROM participants WHERE name=?', (name,)).fetchone()
                if p:
                    session['pid'] = p['id']
                    session['pname'] = name
                    flash(f'다시 오셨군요, {name}님!', 'success')
                    return redirect(url_for('bet'))
            flash('이미 존재하는 이름입니다.', 'error')
            return redirect(url_for('register'))
    return render_template('register.html')


@app.route('/bet', methods=['GET', 'POST'])
def bet():
    if 'pid' not in session:
        return redirect(url_for('register'))
    if get_setting('betting_open') != '1':
        flash('배팅이 마감되었습니다. 대시보드를 확인하세요.', 'info')
        return redirect(url_for('dashboard'))

    pid = session['pid']
    with get_db() as db:
        existing = {b['category'] + '_' + b['choice']: b
                    for b in db.execute('SELECT * FROM bets WHERE participant_id=?', (pid,)).fetchall()}
        existing_list = db.execute('SELECT * FROM bets WHERE participant_id=?', (pid,)).fetchall()

    if request.method == 'POST':
        bets_to_save = []
        total_used = 0
        errors = []

        # 필수: 경기 결과
        r_choice = request.form.get('result_choice')
        r_coins = int(request.form.get('result_coins', 0) or 0)
        if not r_choice:
            errors.append('경기 결과는 필수 배팅입니다.')
        elif r_coins < 200:
            errors.append('경기 결과는 최소 200코인 이상 배팅해야 합니다.')
        else:
            odds = next((o['odds'] for o in CATEGORIES['result']['options'] if o['value'] == r_choice), 0)
            bets_to_save.append(('result', r_choice, r_coins, odds))
            total_used += r_coins

        # 정확한 스코어
        sk = request.form.get('score_k', '').strip()
        sr = request.form.get('score_r', '').strip()
        sc = int(request.form.get('score_coins', 0) or 0)
        if sk != '' and sr != '' and sc >= 100:
            bets_to_save.append(('score', f'{sk}-{sr}', sc, CATEGORIES['score']['odds']))
            total_used += sc

        # 골 득실차, 선취득점, 총득점
        for cat in ['goal_diff', 'first_goal', 'total_goals']:
            ch = request.form.get(f'{cat}_choice')
            cn = int(request.form.get(f'{cat}_coins', 0) or 0)
            if ch and cn >= 100:
                odds = next((o['odds'] for o in CATEGORIES[cat]['options'] if o['value'] == ch), 0)
                bets_to_save.append((cat, ch, cn, odds))
                total_used += cn

        # 스페셜 이벤트
        for ev in SPECIAL_EVENTS:
            v = ev['value']
            if request.form.get(f'special_{v}'):
                cn = int(request.form.get(f'special_{v}_coins', 0) or 0)
                if cn >= 100:
                    bets_to_save.append(('special', v, cn, ev['odds']))
                    total_used += cn

        if total_used != TOTAL_COINS:
            errors.append(f'코인을 정확히 {TOTAL_COINS}개 모두 사용해야 합니다. (현재 {total_used}개 사용)')

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('bet.html', categories=CATEGORIES, special_events=SPECIAL_EVENTS,
                                   existing=existing_list, pname=session.get('pname'),
                                   total_coins=TOTAL_COINS)

        with get_db() as db:
            db.execute('DELETE FROM bets WHERE participant_id=?', (pid,))
            for cat, ch, cn, od in bets_to_save:
                db.execute('INSERT INTO bets (participant_id,category,choice,coins,odds) VALUES (?,?,?,?,?)',
                           (pid, cat, ch, cn, od))
            db.commit()

        flash('배팅 완료! 경기 결과를 기다려봐요 🏆', 'success')
        return redirect(url_for('dashboard'))

    return render_template('bet.html', categories=CATEGORIES, special_events=SPECIAL_EVENTS,
                           existing=existing_list, pname=session.get('pname'),
                           total_coins=TOTAL_COINS)


@app.route('/dashboard')
def dashboard():
    result_set = get_setting('result_set') == '1'
    betting_open = get_setting('betting_open') == '1'

    with get_db() as db:
        participants = db.execute('SELECT * FROM participants ORDER BY created_at').fetchall()
        all_bets = db.execute(
            'SELECT b.*,p.name,p.department FROM bets b JOIN participants p ON b.participant_id=p.id ORDER BY p.name',
        ).fetchall()

    results = calculate_results() if result_set else None

    return render_template('dashboard.html',
                           participants=participants,
                           all_bets=all_bets,
                           betting_open=betting_open,
                           result_set=result_set,
                           results=results,
                           categories=CATEGORIES,
                           special_events=SPECIAL_EVENTS,
                           pid=session.get('pid'))


@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'login':
            if request.form.get('password') == ADMIN_PWD:
                session['admin'] = True
                flash('관리자로 로그인했습니다.', 'success')
            else:
                flash('비밀번호 틀렸어요.', 'error')

        elif action == 'logout':
            session.pop('admin', None)

        elif not session.get('admin'):
            flash('관리자 권한이 필요합니다.', 'error')

        elif action == 'toggle_betting':
            cur = get_setting('betting_open')
            set_setting('betting_open', '0' if cur == '1' else '1')
            flash('배팅 상태 변경 완료.', 'success')

        elif action == 'set_result':
            ks = request.form.get('korea_score')
            rs = request.form.get('rsa_score')
            fg = request.form.get('first_goal')
            if not ks or not rs or not fg:
                flash('모든 필드를 입력해주세요.', 'error')
            else:
                set_setting('korea_score', ks)
                set_setting('rsa_score', rs)
                set_setting('first_goal', fg)
                set_setting('penalty',    '1' if request.form.get('penalty')    else '0')
                set_setting('red_card',   '1' if request.form.get('red_card')   else '0')
                set_setting('comeback',   '1' if request.form.get('comeback')   else '0')
                set_setting('extra_time', '1' if request.form.get('extra_time') else '0')
                set_setting('betting_open', '0')
                set_setting('result_set', '1')
                flash(f'경기 결과 저장 완료! 한국 {ks} : {rs} 남아공', 'success')

        elif action == 'reset':
            with get_db() as db:
                db.execute('DELETE FROM bets')
                db.execute('DELETE FROM participants')
                db.commit()
            for k in ['betting_open','result_set','korea_score','rsa_score',
                      'first_goal','penalty','red_card','comeback','extra_time']:
                set_setting(k, '1' if k == 'betting_open' else ('0' if k != 'korea_score' and k != 'rsa_score' and k != 'first_goal' else None))
            session.pop('pid', None)
            session.pop('pname', None)
            flash('전체 초기화 완료.', 'success')

        return redirect(url_for('admin'))

    is_admin = session.get('admin', False)
    stats = {}
    if is_admin:
        with get_db() as db:
            stats['participants'] = db.execute('SELECT COUNT(*) as c FROM participants').fetchone()['c']
            stats['bets'] = db.execute('SELECT COUNT(*) as c FROM bets').fetchone()['c']
        stats['pool'] = stats['participants'] * 10000
        stats['betting_open'] = get_setting('betting_open') == '1'
        stats['result_set'] = get_setting('result_set') == '1'
        stats['korea_score'] = get_setting('korea_score')
        stats['rsa_score'] = get_setting('rsa_score')

    return render_template('admin.html', is_admin=is_admin, stats=stats)


@app.route('/api/coin_stats')
def coin_stats():
    with get_db() as db:
        count = db.execute('SELECT COUNT(*) as c FROM participants').fetchone()['c']
    return jsonify({'participants': count, 'pool': count * 10000})


init_db()  # gunicorn 기동 시에도 DB 초기화

if __name__ == '__main__':
    import socket
    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except Exception:
        local_ip = '127.0.0.1'
    print('\n' + '='*50)
    print('🏆  어쩌다 월드컵 배팅 시작!')
    print('='*50)
    print(f'📱  내 접속: http://localhost:{PORT}')
    print(f'🌐  팀원 접속: http://{local_ip}:{PORT}')
    print(f'🔧  관리자: http://localhost:{PORT}/admin')
    print(f'🔑  관리자 비밀번호: {ADMIN_PWD}')
    print('='*50 + '\n')
    app.run(debug=False, host='0.0.0.0', port=PORT)
