"""Draw2Seed Research Collector v2.4 — strictly guided, pseudonymous study.

PUBLIC build contains no administration routes. New d2s24_* tables leave legacy
pilot data untouched. The experiment is NOT a production login mechanism.
"""
from __future__ import annotations
import hashlib
import os
import secrets
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from functools import wraps

from flask import Flask, abort, g, jsonify, request, send_from_directory, session
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, create_engine, select, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, scoped_session, sessionmaker

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'src'))
from drawing_rng.verifier import validate_strokes
from drawing_rng.study_v24 import make_references, evaluate, N_ENROLL, VERSION

STUDY_VERSION='d2s24-guided-2026-10'
PRODUCTION=os.getenv('APP_ENV','development')=='production'
SECRET=os.getenv('SECRET_KEY','')
if PRODUCTION and len(SECRET)<32:raise RuntimeError('Set a strong SECRET_KEY in production.')
DATABASE_URL=os.getenv('SUPABASE_DB_URL') or os.getenv('DATABASE_URL','')
TEST_SQLITE=os.getenv('APP_ENV')=='test' and os.getenv('D2S_TEST_ALLOW_SQLITE')=='1'
if DATABASE_URL.startswith('postgres://'):DATABASE_URL='postgresql+psycopg://'+DATABASE_URL[len('postgres://'):]
if DATABASE_URL.startswith('postgresql://'):DATABASE_URL='postgresql+psycopg://'+DATABASE_URL[len('postgresql://'):]
if not DATABASE_URL or not (DATABASE_URL.startswith('postgresql+psycopg://') or (TEST_SQLITE and DATABASE_URL.startswith('sqlite://'))):
    raise RuntimeError('Set SUPABASE_DB_URL to Supabase Session Pooler URI (postgresql+psycopg://).')
if not TEST_SQLITE and not any(v in DATABASE_URL for v in ('sslmode=require','sslmode=verify-full')):
    raise RuntimeError('Add sslmode=require to Supabase connection URL.')
app=Flask(__name__,static_folder=str(ROOT/'static'),static_url_path='/static')
app.config.update(SECRET_KEY=SECRET or secrets.token_urlsafe(48),SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SECURE=PRODUCTION,SESSION_COOKIE_SAMESITE='Lax',
                  PERMANENT_SESSION_LIFETIME=timedelta(days=45),MAX_CONTENT_LENGTH=240_000)
engine=create_engine(DATABASE_URL,pool_pre_ping=True,pool_recycle=1500,
                     connect_args={'check_same_thread':False} if TEST_SQLITE else {'prepare_threshold':None})
DB=scoped_session(sessionmaker(bind=engine,expire_on_commit=False))
class Base(DeclarativeBase):pass

def utc():return datetime.now(timezone.utc)
def new_id():return str(uuid.uuid4())
def aware(dt):return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
def code_hash(s):return hashlib.sha256(s.encode('utf-8')).hexdigest()

class Participant(Base):
    __tablename__='d2s24_participants'
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    recovery_hash:Mapped[str]=mapped_column(String(64),unique=True,index=True)
    consent_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utc)
    consent_version:Mapped[str]=mapped_column(String(50),default=STUDY_VERSION)
    share_reference:Mapped[bool]=mapped_column(Boolean,default=False)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utc)
class StudyEnrollment(Base):
    __tablename__='d2s24_enrollments'
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    participant_id:Mapped[str]=mapped_column(String(36),ForeignKey('d2s24_participants.id'),index=True)
    model:Mapped[dict]=mapped_column(JSON)
    version:Mapped[str]=mapped_column(String(70),default=VERSION)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utc)
class StudySession(Base):
    __tablename__='d2s24_sessions'
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    participant_id:Mapped[str]=mapped_column(String(36),ForeignKey('d2s24_participants.id'),index=True)
    enrollment_id:Mapped[str|None]=mapped_column(String(36),ForeignKey('d2s24_enrollments.id'),nullable=True)
    kind:Mapped[str]=mapped_column(String(25))
    phase:Mapped[str]=mapped_column(String(40))
    index:Mapped[int]=mapped_column(Integer,default=0)
    attack_target_id:Mapped[str|None]=mapped_column(String(36),ForeignKey('d2s24_enrollments.id'),nullable=True)
    started_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utc)
    finished_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
class Drawing(Base):
    __tablename__='d2s24_drawings'
    __table_args__=(UniqueConstraint('session_id','category','trial_index',name='d2s24_one_trial_per_step'),)
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    participant_id:Mapped[str]=mapped_column(String(36),ForeignKey('d2s24_participants.id'),index=True)
    session_id:Mapped[str]=mapped_column(String(36),ForeignKey('d2s24_sessions.id'),index=True)
    enrollment_id:Mapped[str|None]=mapped_column(String(36),ForeignKey('d2s24_enrollments.id'),index=True)
    category:Mapped[str]=mapped_column(String(35),index=True)
    study_phase:Mapped[str]=mapped_column(String(40))
    trial_index:Mapped[int]=mapped_column(Integer)
    strokes:Mapped[list]=mapped_column(JSON)
    dynamics:Mapped[dict]=mapped_column(JSON)
    result:Mapped[dict|None]=mapped_column(JSON,nullable=True)
    device:Mapped[str]=mapped_column(String(32),default='unknown')
    study_version:Mapped[str]=mapped_column(String(50),default=STUDY_VERSION)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utc)
Index('d2s24_session_phase_idx',Drawing.session_id,Drawing.category,Drawing.trial_index)

# Namespaced tables ensure v2.3 frozen pilot records are never overwritten.
Base.metadata.create_all(engine)

@app.teardown_appcontext
def teardown(exc):DB.remove()

@app.after_request
def headers(response):
    response.headers.update({'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY',
      'Referrer-Policy':'no-referrer','Cache-Control':'no-store',
      'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"})
    return response

@app.before_request
def csrf():
    if request.method not in ('POST','PATCH','DELETE'):return
    if not session.get('csrf') or not secrets.compare_digest(request.headers.get('X-D2S-CSRF',''),session['csrf']):
        abort(403,description='Refresh the page before submitting.')
    origin=request.headers.get('Origin')
    if origin and (urlsplit(origin).netloc.lower()!=request.host.lower() or
                   (PRODUCTION and urlsplit(origin).scheme!='https')):
        abort(403,description='Cross-origin request blocked.')

@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(409)
@app.errorhandler(413)
@app.errorhandler(429)
def known_error(e):return jsonify(error=getattr(e,'description',str(e))),getattr(e,'code',400)

def data():
    p=request.get_json(silent=True)
    if not isinstance(p,dict):abort(400,description='Expected JSON object.')
    return p

def participant_required(fn):
    @wraps(fn)
    def inner(*args,**kwargs):
        uid=session.get('pid')
        p=DB().get(Participant,uid) if uid else None
        if p is None:abort(403,description='Start or resume your study session.')
        g.participant=p
        return fn(*args,**kwargs)
    return inner

PHASES=[('practice',2),('enrollment',N_ENROLL),('owner_immediate',6),('near_miss',3),('true_wrong_shape',2),('attack_blind',1),('attack_informed',2)]
FOLLOWUP=[('owner_delayed',6)]
INSTRUCTIONS={
'practice':('Practice your gesture','Draw the same gesture naturally.'),
'enrollment':('Repeat your gesture','Draw the gesture you chose.'),
'owner_immediate':('Draw from memory','Reproduce your own gesture without a reference.'),
'owner_delayed':('Draw from memory','Reproduce the same gesture as last time.'),
'near_miss':('Make a small change','Draw your gesture, but deliberately change one structural detail.'),
'true_wrong_shape':('Draw something different','Draw a completely unrelated gesture.'),
'attack_blind':('Make a blind guess','Draw a gesture that might belong to another participant.'),
'attack_informed':('Copy the shown gesture','Try to reproduce this other participant’s drawing.'),
}

def current_session(p):
    return DB().scalar(select(StudySession).where(StudySession.participant_id==p.id)
                       .order_by(StudySession.started_at.desc()).limit(1))

def get_enrollment(p):
    return DB().scalar(select(StudyEnrollment).where(StudyEnrollment.participant_id==p.id)
                       .order_by(StudyEnrollment.created_at.desc()).limit(1))

def assign_target(s):
    db=DB()
    q=select(StudyEnrollment).join(Participant,StudyEnrollment.participant_id==Participant.id).where(
        Participant.share_reference.is_(True),StudyEnrollment.participant_id!=s.participant_id)
    targets=db.scalars(q).all()
    if not targets:return False
    s.attack_target_id=secrets.choice(targets).id
    return True

def _advance(s):
    protocol=FOLLOWUP if s.kind=='followup' else PHASES
    s.index+=1
    count=dict(protocol)[s.phase]
    if s.index<count:return
    s.index=0
    at=[v[0] for v in protocol].index(s.phase)
    if at==len(protocol)-1:
        s.phase='complete';s.finished_at=utc();return
    s.phase=protocol[at+1][0]
    if s.phase=='attack_blind' and not assign_target(s):
        s.phase='complete';s.finished_at=utc()

def present(s,p):
    if s is None:return {'phase':'welcome','title':'Draw2Seed Study','instruction':'Choose a gesture and repeat it across a short study.'}
    if s.phase=='complete':
        enrolled=get_enrollment(p)
        return {'phase':'complete','title':'Session complete','instruction':'Thank you for participating.',
                'can_return':bool(enrolled and s.kind=='initial' and utc()-aware(s.finished_at)>=timedelta(hours=18)) if s.finished_at else False,
                'session_kind':s.kind}
    title,instruction=INSTRUCTIONS[s.phase]
    total=dict(FOLLOWUP if s.kind=='followup' else PHASES)[s.phase]
    result={'phase':s.phase,'title':title,'instruction':instruction,'step':s.index+1,'total':total,
            'session_kind':s.kind,'show_target':False}
    if s.phase=='attack_informed' and s.attack_target_id:
        target=DB().get(StudyEnrollment,s.attack_target_id)
        owner=DB().get(Participant,target.participant_id) if target else None
        if target and owner and owner.share_reference:
            result['show_target']=True
            result['target_strokes']=target.model['references'][0]
        else:
            # If the owner withdraws challenge sharing, do not strand the volunteer.
            s.phase='complete';s.finished_at=utc();DB().commit()
            return present(s,p)
    return result

@app.get('/')
def index():return send_from_directory(ROOT/'static','index.html')
@app.get('/api/health')
def health():return jsonify(ok=True,version=STUDY_VERSION)
@app.get('/api/state')
def state():
    if not session.get('csrf'):session['csrf']=secrets.token_urlsafe(32)
    p=DB().get(Participant,session.get('pid')) if session.get('pid') else None
    return jsonify(csrf=session['csrf'],study=present(current_session(p),p) if p else present(None,None),
                   registered=bool(p))

@app.post('/api/start')
def start():
    body=data()
    if body.get('consent') is not True:abort(400,description='Study consent is required.')
    if session.get('pid') and DB().get(Participant,session['pid']):abort(409,description='Study already started in this browser.')
    token=secrets.token_urlsafe(24)
    p=Participant(id=new_id(),recovery_hash=code_hash(token),share_reference=body.get('share_reference') is True)
    s=StudySession(id=new_id(),participant_id=p.id,kind='initial',phase='practice',index=0)
    DB().add_all([p,s]);DB().commit()
    session['pid']=p.id;session.permanent=True
    return jsonify(ok=True,recovery_code=token,study=present(s,p)),201

@app.post('/api/resume')
def resume():
    body=data();token=body.get('code','')
    if not isinstance(token,str) or len(token)<24 or len(token)>100:abort(400,description='Invalid return code.')
    p=DB().scalar(select(Participant).where(Participant.recovery_hash==code_hash(token)))
    if not p:abort(403,description='Return code not found.')
    session['pid']=p.id;session.permanent=True
    return jsonify(ok=True,study=present(current_session(p),p))

@app.post('/api/new-browser-participant')
def reset_browser():
    # Only switches browser identity; never deletes research data.
    session.pop('pid',None)
    return jsonify(ok=True)

@app.post('/api/return')
@participant_required
def return_visit():
    p=g.participant
    last=current_session(p);enrollment=get_enrollment(p)
    if not enrollment or not last or last.phase!='complete':abort(409,description='Complete your first session.')
    if last.kind!='initial':abort(409,description='Follow-up session already completed.')
    if not last.finished_at or utc()-aware(last.finished_at)<timedelta(hours=18):
        abort(409,description='Return for a follow-up after at least 18 hours.')
    s=StudySession(id=new_id(),participant_id=p.id,enrollment_id=enrollment.id,
                   kind='followup',phase='owner_delayed',index=0)
    DB().add(s);DB().commit()
    return jsonify(ok=True,study=present(s,p))


def validate_dynamics(raw,strokes):
    """Timing is recorded, not trusted identity evidence or a login factor."""
    if not isinstance(raw,dict):abort(400,description='Missing drawing timing.')
    times=raw.get('times')
    if not isinstance(times,list) or len(times)!=len(strokes):abort(400,description='Timing strokes mismatch.')
    cleaned=[]
    for ts,stroke in zip(times,strokes):
        if not isinstance(ts,list) or len(ts)!=len(stroke):abort(400,description='Timing points mismatch.')
        if any(not isinstance(t,(int,float)) or isinstance(t,bool) or not 0<=t<=600_000 for t in ts):
            abort(400,description='Invalid timing samples.')
        cleaned.append([round(float(t),2) for t in ts])
    ptr=str(raw.get('pointer_type','unknown'))[:16]
    return {'times':cleaned,'pointer_type':ptr,'source':'browser_pointer_events_unattested'}

@app.post('/api/drawing')
@participant_required
def drawing():
    p=g.participant
    # Lock this study session to prevent duplicate/overlapping submissions.
    s=DB().scalar(select(StudySession).where(StudySession.participant_id==p.id)
                  .order_by(StudySession.started_at.desc()).limit(1).with_for_update())
    if not s or s.phase=='complete':abort(409,description='No active drawing task.')
    if s.phase not in INSTRUCTIONS:abort(409,description='No drawing expected.')
    body=data()
    # A retried POST must not silently become the next experimental condition.
    if body.get('phase')!=s.phase or body.get('step')!=s.index+1:
        abort(409,description='This study step has changed. Refresh to continue.')
    try:strokes=validate_strokes(body.get('strokes'))
    except (ValueError,TypeError,OverflowError,IndexError) as e:abort(400,description=str(e))
    timings=validate_dynamics(body.get('dynamics'),strokes)
    db=DB()
    # Server determines category. Never trust participant-supplied labels.
    phase=s.phase
    enrollment=db.get(StudyEnrollment,s.enrollment_id) if s.enrollment_id else None
    if phase in ('owner_immediate','owner_delayed','near_miss','true_wrong_shape') and not enrollment:
        abort(409,description='Enrollment not finalized.')
    if phase in ('attack_blind','attack_informed'):
        enrollment=db.get(StudyEnrollment,s.attack_target_id) if s.attack_target_id else None
        if not enrollment or enrollment.participant_id==p.id:
            abort(409,description='No adversarial target assigned.')
        owner=db.get(Participant,enrollment.participant_id)
        if not owner or not owner.share_reference:abort(409,description='Target unavailable.')
    result=None
    if enrollment and phase not in ('practice','enrollment'):
        result=evaluate(enrollment.model,strokes)
    category={'owner_immediate':'owner_test','owner_delayed':'owner_test',
              'attack_blind':'blind_impostor','attack_informed':'informed_forgery'}.get(phase,phase)
    row=Drawing(id=new_id(),participant_id=p.id,session_id=s.id,
                enrollment_id=enrollment.id if enrollment else None,
                category=category,study_phase=phase,trial_index=s.index+1,strokes=strokes,dynamics=timings,
                result=result,device=timings['pointer_type'])
    db.add(row)
    if phase=='enrollment' and s.index+1==N_ENROLL:
        older=db.scalars(select(Drawing).where(Drawing.session_id==s.id,Drawing.category=='enrollment',Drawing.trial_index<N_ENROLL)
                         .order_by(Drawing.trial_index.asc())).all()
        model=make_references([r.strokes for r in older]+[strokes])
        enrollment=StudyEnrollment(id=new_id(),participant_id=p.id,model=model)
        db.add(enrollment);db.flush()
        s.enrollment_id=enrollment.id
        for old in older:old.enrollment_id=enrollment.id
        row.enrollment_id=enrollment.id
    _advance(s)
    # The tasks intentionally do not reveal acceptance or similarity.
    db.commit()
    return jsonify(ok=True,study=present(s,p),recorded=True)

@app.post('/api/delete-my-data')
@participant_required
def delete_data():
    p=g.participant;db=DB()
    # Explicit cleanup order: avoid relying on preexisting Supabase FK rules.
    own_ids=db.scalars(select(StudyEnrollment.id).where(StudyEnrollment.participant_id==p.id)).all()
    # Anonymize other participants' attack assignments to withdrawn targets.
    if own_ids:
        from sqlalchemy import update
        db.execute(update(StudySession).where(StudySession.attack_target_id.in_(own_ids)).values(attack_target_id=None))
    if own_ids:
        # Other participants' forgeries against withdrawn templates must also go.
        for d in db.scalars(select(Drawing).where(Drawing.enrollment_id.in_(own_ids))).all():db.delete(d)
    db.flush()
    for d in db.scalars(select(Drawing).where(Drawing.participant_id==p.id)).all():db.delete(d)
    db.flush()
    for s in db.scalars(select(StudySession).where(StudySession.participant_id==p.id)).all():db.delete(s)
    db.flush()
    for e in db.scalars(select(StudyEnrollment).where(StudyEnrollment.participant_id==p.id)).all():db.delete(e)
    db.flush();db.delete(p);db.commit()
    session.pop('pid',None)
    return jsonify(ok=True)

# NO DEV ADMIN IMPORT/ROUTES EXIST IN THE PUBLIC CODEBASE.
if __name__=='__main__':
    app.run(host='127.0.0.1',port=int(os.getenv('PORT','5000')),debug=False)
