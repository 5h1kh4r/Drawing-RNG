"""Anonymous Draw2Seed research prototype.

No volunteer accounts or credentials. Browser-bound pseudonymous sessions group
enrollments and attempts. Research data is not secure login or cryptographic entropy.
"""
from __future__ import annotations
import os
import secrets
import sys
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, g, jsonify, request, send_from_directory, session
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, create_engine, func, select, delete
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, scoped_session, sessionmaker

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'src'))
from drawing_rng.verifier import enroll, verify, features, comparison, validate_strokes, _structural_difference
from drawing_rng.research_features import discover_boundary, audit_gallery_promotion

ALGORITHM_VERSION = 'lean-v2.3-anonymous-2026-10'
CONSENT_VERSION = 'volunteer-v2.3-2026-10'

PRODUCTION=os.getenv('APP_ENV','development').lower() == 'production'
SECRET=os.getenv('SECRET_KEY')
if PRODUCTION and (not SECRET or len(SECRET)<32):
    raise RuntimeError('Production requires a strong SECRET_KEY (at least 32 characters).')
# Supabase is the primary store in both the public and local development builds.
# Use the Project > Connect > Postgres connection URI (not an anon/service-role API key).
DATABASE_URL=os.getenv('SUPABASE_DB_URL') or os.getenv('DATABASE_URL','')
ALLOW_TEST_SQLITE=os.getenv('D2S_TEST_ALLOW_SQLITE')=='1' and os.getenv('APP_ENV')=='test'
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL='postgresql+psycopg://'+DATABASE_URL[len('postgres://'):]
if DATABASE_URL.startswith('postgresql://'):
    DATABASE_URL='postgresql+psycopg://'+DATABASE_URL[len('postgresql://'):]
if not DATABASE_URL or not (DATABASE_URL.startswith('postgresql+psycopg://') or
                           (ALLOW_TEST_SQLITE and DATABASE_URL.startswith('sqlite://'))):
    raise RuntimeError('Set SUPABASE_DB_URL to the Supabase PostgreSQL connection URI. SQLite is only allowed in isolated tests.')
if not ALLOW_TEST_SQLITE and 'sslmode=require' not in DATABASE_URL and 'sslmode=verify-full' not in DATABASE_URL:
    raise RuntimeError('The Supabase PostgreSQL connection must require TLS (?sslmode=require).')

app=Flask(__name__,static_folder=str(ROOT/'static'), static_url_path='/static')
app.config.update(SECRET_KEY=SECRET or secrets.token_hex(32),
                  SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                  SESSION_COOKIE_SECURE=PRODUCTION,
                  PERMANENT_SESSION_LIFETIME=timedelta(days=5),
                  MAX_CONTENT_LENGTH=160_000)
engine=create_engine(DATABASE_URL, pool_pre_ping=True, connect_args={'check_same_thread':False} if ALLOW_TEST_SQLITE else {'prepare_threshold':None})
DBSession=scoped_session(sessionmaker(bind=engine,expire_on_commit=False))

class Base(DeclarativeBase): pass

class User(Base):
    __tablename__='d2s_users'
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    username: Mapped[str]=mapped_column(String(40),unique=True,index=True)
    password_hash: Mapped[str]=mapped_column(String(255)) # Legacy schema field: always 'LOGIN_DISABLED', never a password
    consent_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    consent_version: Mapped[str]=mapped_column(String(50),default=CONSENT_VERSION)
    failed_logins: Mapped[int]=mapped_column(Integer,default=0)
    locked_until: Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

class Enrollment(Base):
    __tablename__='d2s_enrollments'
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    user_id: Mapped[str]=mapped_column(String(36),ForeignKey('d2s_users.id'),index=True)
    label: Mapped[str]=mapped_column(String(70))
    model: Mapped[dict]=mapped_column(JSON)
    algorithm_version: Mapped[str]=mapped_column(String(50),default=ALGORITHM_VERSION)
    stability: Mapped[float]
    robustness: Mapped[dict]=mapped_column(JSON)
    device: Mapped[str]=mapped_column(String(30),default='unknown')
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

class Attempt(Base):
    __tablename__='d2s_attempts'
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    enrollment_id: Mapped[str]=mapped_column(String(36),ForeignKey('d2s_enrollments.id'),index=True)
    user_id: Mapped[str]=mapped_column(String(36),ForeignKey('d2s_users.id'),index=True)
    category: Mapped[str]=mapped_column(String(30))
    algorithm_version: Mapped[str]=mapped_column(String(50),default=ALGORITHM_VERSION)
    strokes: Mapped[list]=mapped_column(JSON)
    result: Mapped[dict]=mapped_column(JSON)
    device: Mapped[str]=mapped_column(String(30),default='unknown')
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

class GallerySample(Base):
    __tablename__='d2s_gallery'
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    enrollment_id: Mapped[str]=mapped_column(String(36),ForeignKey('d2s_enrollments.id'),index=True)
    user_id: Mapped[str]=mapped_column(String(36),ForeignKey('d2s_users.id'),index=True)
    strokes: Mapped[list]=mapped_column(JSON)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

class ForbiddenGesture(Base):
    __tablename__='d2s_forbidden'
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    enrollment_id: Mapped[str]=mapped_column(String(36),ForeignKey('d2s_enrollments.id'),index=True)
    user_id: Mapped[str]=mapped_column(String(36),ForeignKey('d2s_users.id'),index=True)
    label: Mapped[str]=mapped_column(String(70))
    strokes: Mapped[list]=mapped_column(JSON)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

class ChallengeOptIn(Base):
    __tablename__='d2s_challenge_optin'
    enrollment_id: Mapped[str]=mapped_column(String(36),ForeignKey('d2s_enrollments.id'),primary_key=True)
    user_id: Mapped[str]=mapped_column(String(36),ForeignKey('d2s_users.id'),index=True)
    enabled: Mapped[bool]=mapped_column(Boolean,default=False)

class Challenge(Base):
    __tablename__='d2s_challenges'
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    attacker_id: Mapped[str]=mapped_column(String(36),ForeignKey('d2s_users.id'),index=True)
    enrollment_id: Mapped[str]=mapped_column(String(36),ForeignKey('d2s_enrollments.id'),index=True)
    mode: Mapped[str]=mapped_column(String(30))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
    expires_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    attempt_id: Mapped[str|None]=mapped_column(String(36),nullable=True)

class BoundaryReport(Base):
    __tablename__='d2s_boundary_reports'
    enrollment_id: Mapped[str]=mapped_column(String(36),ForeignKey('d2s_enrollments.id'),primary_key=True)
    report: Mapped[dict]=mapped_column(JSON)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

Base.metadata.create_all(engine)


@app.teardown_appcontext
def _close_db(exc): DBSession.remove()

@app.after_request
def security_headers(response):
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['Cache-Control']='no-store'
    response.headers['Content-Security-Policy']=("default-src 'self'; script-src 'self'; style-src 'self'; "
                                                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
    return response

@app.before_request
def csrf_check():
    if request.method in ('POST','PUT','PATCH','DELETE'):
        # A custom header prevents cross-origin form submissions. CSRF token is
        # bound to the signed, SameSite=Lax Flask session cookie.
        if not session.get('csrf') or not secrets.compare_digest(
                request.headers.get('X-D2S-CSRF',''),session['csrf']):
            abort(403,description='Invalid CSRF token. Refresh the page.')
        origin=request.headers.get('Origin')
        if origin and (urlsplit(origin).netloc.lower()!=request.host.lower() or
                       (PRODUCTION and urlsplit(origin).scheme!='https')):
            abort(403,description='Cross-origin request blocked.')

@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(409)
@app.errorhandler(410)
@app.errorhandler(413)
@app.errorhandler(429)
def handled(e):return jsonify(error=getattr(e,'description',str(e))),getattr(e,'code',400)

def need_participant(fn):
    @wraps(fn)
    def inner(*a,**kw):
        uid=session.get('uid')
        user=DBSession().get(User,uid) if uid else None
        if user is None:
            if request.endpoint=='list_enrollments':
                return jsonify(enrollments=[])
            # First enrollment creates a pseudonymous session only with explicit consent.
            if request.endpoint=='make_enrollment':
                p=payload()
                if p.get('consent') is not True:
                    abort(400,description='Read and acknowledge the study consent before enrolling.')
                user=User(id=str(uuid.uuid4()),username='p_'+secrets.token_hex(8),
                          password_hash='LOGIN_DISABLED',
                          consent_at=datetime.now(timezone.utc),consent_version=CONSENT_VERSION)
                DBSession().add(user)
                DBSession().flush()
                session['uid']=user.id
                session.permanent=True
            else:
                return jsonify(error='Enroll a gesture before using this feature.'),403
        if user.consent_version!=CONSENT_VERSION and user.consent_version!='volunteer-v2.2-2026-10':
            return jsonify(error='Study consent is required.'),403
        g.user=user
        return fn(*a,**kw)
    return inner

def payload():
    p=request.get_json(silent=True)
    if not isinstance(p,dict):abort(400,description='Expected JSON object.')
    return p

def owned_enrollment(enrollment_id):
    e=DBSession().scalar(select(Enrollment).where(Enrollment.id==enrollment_id,Enrollment.user_id==g.user.id))
    if e is None:abort(404,description='Enrollment not found.')
    return e

def summary(e):
    db=DBSession()
    count=db.scalar(select(func.count()).select_from(Attempt).where(Attempt.enrollment_id==e.id))
    gallery=db.scalar(select(func.count()).select_from(GallerySample).where(GallerySample.enrollment_id==e.id))
    shared=db.scalar(select(ChallengeOptIn.enabled).where(ChallengeOptIn.enrollment_id==e.id)) or False
    forbidden_count=db.scalar(select(func.count()).select_from(ForbiddenGesture).where(ForbiddenGesture.enrollment_id==e.id))
    attack_count=db.scalar(select(func.count()).select_from(Attempt).where(Attempt.enrollment_id==e.id,Attempt.user_id!=e.user_id))
    return {'id':e.id,'label':e.label,'stability':e.stability,'robustness':e.robustness,
            'challenge_opt_in':bool(shared),'forbidden_count':forbidden_count,'received_challenges':attack_count,
            'warnings':e.model.get('warnings',[]), 'created_at':e.created_at.isoformat(),
            'attempt_count':count,'gallery_count':gallery,'device':e.device}

@app.get('/')
def index():return send_from_directory(ROOT/'static','index.html')

@app.get('/api/session')
def session_info():
    if not session.get('csrf'):session['csrf']=secrets.token_urlsafe(30)
    uid=session.get('uid')
    user=DBSession().get(User,uid) if uid else None
    if uid and not user:
        session.pop('uid',None)
    return jsonify(csrf=session['csrf'],participant_id=user.username if user else None,
                   has_profile=bool(user),consent_required=not bool(user),
                   note='Anonymous browser session; clearing cookies may lose access to your saved gestures.')

@app.post('/api/fresh-session')
def fresh_session():
    # Useful when several volunteers share a lab machine. The old data remains
    # in the research database; resetting the cookie is NOT a deletion request.
    session.clear()
    session['csrf']=secrets.token_urlsafe(30)
    return jsonify(ok=True,csrf=session['csrf'],
                   warning='Started a new anonymous browser session. Older data was not deleted.')

@app.get('/api/enrollments')
@need_participant
def list_enrollments():
    db=DBSession();els=db.scalars(select(Enrollment).where(Enrollment.user_id==g.user.id)
                               .order_by(Enrollment.created_at.desc())).all()
    return jsonify(enrollments=[summary(e) for e in els])

@app.post('/api/enrollments')
@need_participant
def make_enrollment():
    p=payload();label=str(p.get('label','')).strip()[:70]
    if not label:abort(400,description='Give your gesture a short label.')
    db=DBSession()
    if db.scalar(select(func.count()).select_from(Enrollment).where(Enrollment.user_id==g.user.id))>=12:
        return jsonify(error='Maximum 12 enrollments per participant.'),429
    try:model=enroll(p.get('attempts'))
    except (ValueError,TypeError,OverflowError,IndexError) as e:return jsonify(error=str(e)),400
    e=Enrollment(id=str(uuid.uuid4()),user_id=g.user.id,label=label,model=model,
                 stability=model['stability'],robustness=model['robustness'],
                 device=str(p.get('device','unknown'))[:30])
    db.add(e);db.commit()
    return jsonify(ok=True,enrollment=summary(e)),201

@app.post('/api/enrollments/<enrollment_id>/attempts')
@need_participant
def submit_attempt(enrollment_id):
    e=owned_enrollment(enrollment_id)
    p=payload()
    cat=str(p.get('category','owner_test'))
    if cat not in ('owner_test','near_miss','true_wrong_shape','bad_sample'):
        abort(400,description='Unrecognized attempt label.')
    db=DBSession();now=datetime.now(timezone.utc)
    recent=db.scalar(select(func.count()).select_from(Attempt).where(
        Attempt.user_id==g.user.id,Attempt.created_at>=now-timedelta(hours=1)))
    if recent>=int(os.getenv('ATTEMPTS_PER_HOUR','120')):
        return jsonify(error='Too many attempts this hour.'),429
    galleries=db.scalars(select(GallerySample).where(GallerySample.enrollment_id==e.id)
                         .order_by(GallerySample.created_at.asc(),GallerySample.id.asc())).all()
    forbidden=db.scalars(select(ForbiddenGesture).where(ForbiddenGesture.enrollment_id==e.id)).all()
    refs=[x.strokes for x in galleries]
    model=model_with_negatives(e.model, forbidden)
    enforce=os.getenv('COUNTERFACTUAL_ENFORCE','0')=='1'
    try:
        clean_strokes=validate_strokes(p.get('strokes'))
        result=verify(model,clean_strokes,refs,enforce)
    except (ValueError,TypeError,OverflowError,IndexError) as err:return jsonify(error=str(err)),400
    updated=False;gallery_reason='not_eligible';audit=None
    if cat=='owner_test' and result['update_eligible'] and os.getenv('AUTO_GALLERY_UPDATES','1')=='1':
        # Only directly verified matches to the immutable enrollment may update.
        anchor=comparison(features(e.model['canonical']),features(clean_strokes))
        if anchor['token_score']>=.68 and anchor['geometry_pass']:
            duplicate=any(comparison(features(gs.strokes),features(clean_strokes))['token_score']>.96
                          for gs in galleries)
            if duplicate:
                gallery_reason='near_duplicate'
            else:
                audit=audit_gallery_promotion(model,refs,clean_strokes)
                if audit['safe']:
                    if len(galleries)>=6:db.delete(galleries[0])
                    db.add(GallerySample(id=str(uuid.uuid4()),enrollment_id=e.id,user_id=g.user.id,
                                         strokes=clean_strokes))
                    updated=True;gallery_reason='promoted'
                else:
                    gallery_reason='blocked_by_negative_regression'
    # Persist the final result as a new object, including audit information.
    result={**result,'gallery_updated':updated,'gallery_update_reason':gallery_reason,
            'gallery_update_audit':audit}
    a=Attempt(id=str(uuid.uuid4()),enrollment_id=e.id,user_id=g.user.id,
              category=cat,strokes=clean_strokes,result=result,device=str(p.get('device','unknown'))[:30])
    db.add(a);db.commit()
    return jsonify(ok=True,attempt_id=a.id,result=result)

@app.get('/api/enrollments/<enrollment_id>/attempts')
@need_participant
def list_attempts(enrollment_id):
    e=owned_enrollment(enrollment_id)
    db=DBSession()
    rows=db.scalars(select(Attempt).where(Attempt.enrollment_id==e.id,Attempt.user_id==g.user.id)
                   .order_by(Attempt.created_at.desc()).limit(100)).all()
    return jsonify(attempts=[{'id':r.id,'category':r.category,'created_at':r.created_at.isoformat(),
                              'accepted':r.result['accepted'],'token_score':r.result['token_score'],
                              'geometry_score':r.result['geometry_score'],
                              'gallery_rescue':r.result['gallery_rescue']} for r in rows])

def model_with_negatives(base_model, forbidden):
    # Copy the list only: never mutate a stored canonical model or its negative bank.
    return {**base_model,'negative_bank':list(base_model.get('negative_bank', [])) +
            [{'kind':'user_defined','class':'participant_negative','strokes':f.strokes}
             for f in forbidden]}

@app.get('/api/enrollments/<enrollment_id>/forbidden')
@need_participant
def forbidden_list(enrollment_id):
    owned_enrollment(enrollment_id)
    items=DBSession().scalars(select(ForbiddenGesture).where(
        ForbiddenGesture.enrollment_id==enrollment_id,ForbiddenGesture.user_id==g.user.id)
        .order_by(ForbiddenGesture.created_at.asc())).all()
    return jsonify(forbidden=[{'id':f.id,'label':f.label,'strokes':f.strokes,
                               'created_at':f.created_at.isoformat()} for f in items])

@app.get('/api/enrollments/<enrollment_id>/reference')
@need_participant
def owner_reference(enrollment_id):
    e=owned_enrollment(enrollment_id)
    return jsonify(strokes=e.model['canonical'])

@app.post('/api/enrollments/<enrollment_id>/forbidden')
@need_participant
def forbidden_add(enrollment_id):
    e=owned_enrollment(enrollment_id)
    db=DBSession();p=payload()
    label=str(p.get('label','')).strip()[:70]
    if not label:abort(400,description='Label the forbidden variant.')
    if db.scalar(select(func.count()).select_from(ForbiddenGesture).where(
            ForbiddenGesture.enrollment_id==e.id))>=5:
        abort(429,description='Maximum five forbidden gestures per enrollment.')
    try:
        strokes=validate_strokes(p.get('strokes'))
        comp=comparison(features(e.model['canonical']),features(strokes))
    except (ValueError,TypeError,OverflowError,IndexError) as err:
        return jsonify(error=str(err)),400
    if strokes==e.model['canonical'] or comp['token_score'] >= .99999 and comp['geometry_score']>=.99999:
        abort(400,description='This is indistinguishable from the reference. Draw a meaningful incorrect variant.')
    # This user-supplied label is not proof of objective structural incorrectness.
    new=ForbiddenGesture(id=str(uuid.uuid4()),enrollment_id=e.id,user_id=g.user.id,
                         label=label,strokes=strokes)
    db.add(new);db.commit()
    return jsonify(ok=True,id=new.id,reference_token_similarity=comp['token_score'],
                   warning='Participant-designated negative; no independent ground-truth validation.'),201

@app.delete('/api/enrollments/<enrollment_id>/forbidden/<negative_id>')
@need_participant
def forbidden_remove(enrollment_id,negative_id):
    owned_enrollment(enrollment_id)
    db=DBSession();neg=db.scalar(select(ForbiddenGesture).where(ForbiddenGesture.id==negative_id,
                             ForbiddenGesture.enrollment_id==enrollment_id,ForbiddenGesture.user_id==g.user.id))
    if neg is None:abort(404,description='Negative reference not found.')
    db.delete(neg);db.commit()
    return jsonify(ok=True)

@app.post('/api/enrollments/<enrollment_id>/boundary')
@need_participant
def boundary_analyze(enrollment_id):
    e=owned_enrollment(enrollment_id);db=DBSession()
    cached=db.get(BoundaryReport,e.id)
    if cached:return jsonify(ok=True,report=cached.report,cached=True)
    galleries=db.scalars(select(GallerySample).where(GallerySample.enrollment_id==e.id)
                         .order_by(GallerySample.created_at.asc())).all()
    report=discover_boundary(e.model,[item.strokes for item in galleries])
    report['gallery_size_at_probe']=len(galleries)
    report['snapshot_at']=datetime.now(timezone.utc).isoformat()
    db.add(BoundaryReport(enrollment_id=e.id,report=report));db.commit()
    return jsonify(ok=True,report=report,cached=False)

@app.post('/api/enrollments/<enrollment_id>/challenge-sharing')
@need_participant
def challenge_sharing(enrollment_id):
    e=owned_enrollment(enrollment_id)
    enabled=payload().get('enabled')
    if not isinstance(enabled,bool):abort(400,description='Expected boolean enabled value.')
    db=DBSession();row=db.get(ChallengeOptIn,e.id)
    if row is None:
        db.add(ChallengeOptIn(enrollment_id=e.id,user_id=g.user.id,enabled=enabled))
    else:row.enabled=enabled
    db.commit()
    return jsonify(ok=True,enabled=enabled,
                   note='Disabling stops new access, but cannot retract previews already viewed by others.')

CHALLENGE_MODES=('blind_impostor','informed_forgery','near_miss')

def time_utc(dt):
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)

def challenge_view(ch):
    data={'id':ch.id,'mode':ch.mode,'expires_at':ch.expires_at.isoformat(),
          'instructions':('Draw what you think an unknown enrolled gesture might look like.'
                          if ch.mode=='blind_impostor' else
                          'Draw a deliberately structurally different near miss of this gesture.'
                          if ch.mode=='near_miss' else
                          'Try to reproduce this gesture as accurately as possible.')}
    if ch.mode!='blind_impostor':
        e=DBSession().get(Enrollment,ch.enrollment_id)
        data['target_strokes']=e.model['canonical']
    return data

def active_challenge():
    return DBSession().scalar(select(Challenge).where(
        Challenge.attacker_id==g.user.id,Challenge.submitted_at.is_(None),
        Challenge.expires_at>datetime.now(timezone.utc)).order_by(Challenge.created_at.desc()).limit(1))

@app.get('/api/challenges')
@need_participant
def challenge_status():
    current=active_challenge()
    if current:
        shared=DBSession().scalar(select(ChallengeOptIn.enabled).where(
            ChallengeOptIn.enrollment_id==current.enrollment_id))
        if not shared:current=None
    total=DBSession().scalar(select(func.count()).select_from(Challenge).where(
        Challenge.attacker_id==g.user.id,Challenge.submitted_at.is_not(None)))
    return jsonify(active=challenge_view(current) if current else None,submitted_count=total)

@app.post('/api/challenges/assign')
@need_participant
def assign_challenge():
    mode=str(payload().get('mode',''))
    if mode not in CHALLENGE_MODES:abort(400,description='Invalid challenge mode.')
    db=DBSession();current=active_challenge()
    if current:
        # Never silently swap challenge modes; restrict target scraping.
        shared=db.scalar(select(ChallengeOptIn.enabled).where(
            ChallengeOptIn.enrollment_id==current.enrollment_id))
        if shared:
            return jsonify(ok=True,challenge=challenge_view(current),reused=True)
    now=datetime.now(timezone.utc)
    created=db.scalar(select(func.count()).select_from(Challenge).where(
        Challenge.attacker_id==g.user.id,Challenge.created_at>=now-timedelta(days=1)))
    if created>=12:abort(429,description='Daily challenge-assignment limit reached.')
    # Database chooses the target, excluding the attacker's own enrollments.
    target=db.scalar(select(Enrollment).join(ChallengeOptIn,ChallengeOptIn.enrollment_id==Enrollment.id)
                     .where(ChallengeOptIn.enabled.is_(True),Enrollment.user_id!=g.user.id)
                     .order_by(func.random()).limit(1))
    if not target:
        return jsonify(error='No volunteer gestures have been opted in for challenges yet.'),404
    challenge=Challenge(id=str(uuid.uuid4()),attacker_id=g.user.id,enrollment_id=target.id,
                        mode=mode,expires_at=now+timedelta(minutes=30))
    db.add(challenge);db.commit()
    return jsonify(ok=True,challenge=challenge_view(challenge),reused=False),201

@app.post('/api/challenges/<challenge_id>/submit')
@need_participant
def challenge_submit(challenge_id):
    db=DBSession();ch=db.scalar(select(Challenge).where(
        Challenge.id==challenge_id,Challenge.attacker_id==g.user.id))
    if ch is None:abort(404,description='Challenge not found.')
    if ch.submitted_at is not None:abort(409,description='Challenge was already submitted.')
    if time_utc(ch.expires_at) < datetime.now(timezone.utc):abort(410,description='Challenge expired.')
    share=db.scalar(select(ChallengeOptIn.enabled).where(
        ChallengeOptIn.enrollment_id==ch.enrollment_id))
    if not share:abort(403,description='The target is no longer available for research challenges.')
    target=db.get(Enrollment,ch.enrollment_id)
    if target is None:abort(404,description='Target no longer exists.')
    try:
        clean=validate_strokes(payload().get('strokes'))
        gallery=db.scalars(select(GallerySample).where(GallerySample.enrollment_id==target.id)).all()
        forbidden=db.scalars(select(ForbiddenGesture).where(ForbiddenGesture.enrollment_id==target.id)).all()
        result=verify(model_with_negatives(target.model,forbidden),clean,
                      [x.strokes for x in gallery],os.getenv('COUNTERFACTUAL_ENFORCE','0')=='1')
        result['observed_topology_change']=_structural_difference(
            features(target.model['canonical']),features(clean))
        result['challenge_mode']=ch.mode
    except (ValueError,TypeError,OverflowError,IndexError) as err:
        return jsonify(error=str(err)),400
    a=Attempt(id=str(uuid.uuid4()),enrollment_id=target.id,user_id=g.user.id,
              category=ch.mode,strokes=clean,result=result,device='challenge')
    ch.submitted_at=datetime.now(timezone.utc);ch.attempt_id=a.id
    db.add(a);db.commit()
    # No match scores or success/failure feedback to adversaries; prevents oracle-like tuning.
    return jsonify(ok=True,recorded=True,message='Challenge submission recorded. Results are withheld during the study.')

def delete_enrollment_data(db, enrollment_id):
    # Erase all records related to an enrollment, including submissions by other users.
    db.execute(delete(Challenge).where(Challenge.enrollment_id==enrollment_id))
    db.execute(delete(Attempt).where(Attempt.enrollment_id==enrollment_id))
    db.execute(delete(GallerySample).where(GallerySample.enrollment_id==enrollment_id))
    db.execute(delete(ForbiddenGesture).where(ForbiddenGesture.enrollment_id==enrollment_id))
    db.execute(delete(BoundaryReport).where(BoundaryReport.enrollment_id==enrollment_id))
    db.execute(delete(ChallengeOptIn).where(ChallengeOptIn.enrollment_id==enrollment_id))
    db.execute(delete(Enrollment).where(Enrollment.id==enrollment_id))


def delete_user_data(db, user_id):
    owned=db.scalars(select(Enrollment.id).where(Enrollment.user_id==user_id)).all()
    for enrollment_id in owned:
        delete_enrollment_data(db,enrollment_id)
    db.execute(delete(Challenge).where(Challenge.attacker_id==user_id))
    db.execute(delete(Attempt).where(Attempt.user_id==user_id))
    db.execute(delete(User).where(User.id==user_id))


@app.delete('/api/participant')
@need_participant
def delete_participant():
    if payload().get('confirm')!='DELETE':
        abort(400,description='Type DELETE to confirm removal of this browser profile and all linked study data.')
    db=DBSession()
    delete_user_data(db,g.user.id)
    db.commit();session.clear()
    return jsonify(ok=True,deleted=True)

@app.get('/api/health')
def health():return jsonify(ok=True,service='draw2seed-volunteer',version=ALGORITHM_VERSION)

if __name__=='__main__':
    app.run(host='127.0.0.1',port=int(os.getenv('PORT','5000')),debug=False)
