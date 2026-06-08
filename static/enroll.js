const canvas = document.getElementById('canvas');
const c = setupCanvas(canvas, document.getElementById('count'));

const verifyCanvas = document.getElementById('verifyCanvas');
const v = setupCanvas(verifyCanvas, document.getElementById('verifyCount'));

const pid = document.getElementById('pid');
pid.textContent = makeParticipantId();
document.getElementById('newPid').onclick = () => pid.textContent = newParticipantId();

let attempts = [];
let analysisResult = null;
let verifyResult = null;

function setSteps() {
  for (let i = 1; i <= 4; i++) document.getElementById('s' + i).classList.remove('active');
  document.getElementById('s' + Math.min(attempts.length + 1, 4)).classList.add('active');
  document.getElementById('attemptCount').textContent = attempts.length;
}

function out(t, cls = '') {
  const el = document.getElementById('result');
  el.textContent = t;
  el.className = 'output ' + cls;
}

function verifyOut(t, cls = '') {
  const el = document.getElementById('verifyResult');
  el.textContent = t;
  el.className = 'output ' + cls;
}

function setVaultPill(text, cls = 'neutral') {
  const pill = document.getElementById('vaultStatusPill');
  pill.textContent = text;
  pill.className = 'status-pill ' + cls;
}

function showVaultSection() {
  document.getElementById('vaultSection').classList.remove('hidden');
  document.getElementById('vaultOutputs').classList.add('hidden');
  document.getElementById('verifyJson').textContent = '{}';
  setVaultPill('Ready', 'neutral');
  verifyOut('Redraw the enrolled secret and click Unlock Vault.');
}

function hideVaultSection() {
  document.getElementById('vaultSection').classList.add('hidden');
  document.getElementById('vaultOutputs').classList.add('hidden');
  verifyResult = null;
  v.clear();
}

function renderPalette(containerId, palette) {
  const pal = document.getElementById(containerId);
  pal.innerHTML = '';
  if (!palette) return;
  for (const [name, value] of Object.entries(palette)) {
    const wrap = document.createElement('div');
    wrap.className = 'swatch-wrap';

    const d = document.createElement('div');
    d.className = 'swatch';
    d.style.background = value;
    d.title = name + ': ' + value;

    const label = document.createElement('span');
    label.className = 'swatch-label';
    label.textContent = name + ' ' + value;

    wrap.appendChild(d);
    wrap.appendChild(label);
    pal.appendChild(wrap);
  }
}

function renderResult(r) {
  const lines = [];
  lines.push('Status: ' + (r.accepted_for_demo ? 'ACCEPTED FOR DEMO' : 'NOT STABLE ENOUGH'));
  lines.push('Stability score: ' + Number(r.stability_score || 0).toFixed(3));
  lines.push('Label: ' + r.stability_label);
  lines.push('Recommended profile: ' + r.recommended_profile);
  lines.push('Central attempt: ' + r.central_attempt);
  lines.push('Canonical token count: ' + r.canonical_token_count);
  if (r.geometry_stability_score !== undefined) {
    lines.push('Geometry stability: ' + Number(r.geometry_stability_score || 0).toFixed(3));
  }
  lines.push('Warnings: ' + ((r.warnings || []).length ? r.warnings.join(', ') : 'none'));
  if (r.fuzzy_enabled) {
    lines.push('Fuzzy helper: enabled (prototype)');
  }
  lines.push('');

  if (r.enrollment_id) {
    lines.push('Saved enrollment ID: ' + r.enrollment_id);
  }
  if (r.enrollment_log_error) {
    lines.push('Enrollment log error: ' + r.enrollment_log_error);
  }

  if (r.outputs) {
    lines.push('Seed hex: ' + r.outputs.seed_hex);
    lines.push('Demo password for ' + r.outputs.domain + ': ' + r.outputs.demo_password);
  }

  out(lines.join('\n'), r.accepted_for_demo ? 'ok' : 'warn');
  document.getElementById('jsonResult').textContent = JSON.stringify(r, null, 2);
  renderPalette('palette', r.outputs && r.outputs.avatar_palette);

  if (r.accepted_for_demo) {
    localStorage.setItem('drng_last_enrollment_result', JSON.stringify(r));
    showVaultSection();
  } else {
    hideVaultSection();
  }
}

function renderUnlockSuccess(r) {
  const outputs = r.outputs || {};
  const lines = [];
  lines.push('ACCESS GRANTED');
  lines.push('Final score: ' + Number(r.final_score ?? r.score ?? 0).toFixed(3));
  lines.push('Token score: ' + Number(r.token_score ?? r.score ?? 0).toFixed(3));
  if (r.geometry_scores) {
    lines.push('Layout score: ' + Number(r.geometry_scores.layout || 0).toFixed(3));
    lines.push('Relation score: ' + Number(r.geometry_scores.relation || 0).toFixed(3));
    lines.push('Curve score: ' + Number(r.geometry_scores.curve || 0).toFixed(3));
    lines.push('Shape score: ' + Number(r.geometry_scores.stroke_shape || 0).toFixed(3));
  }
  lines.push('Token threshold: ' + Number(r.threshold || 0).toFixed(3));
  lines.push('Profile: ' + r.profile);
  if (r.verification_id) lines.push('Logged verification ID: ' + r.verification_id);
  if (r.verification_log_error) lines.push('Verification log error: ' + r.verification_log_error);
  if (r.fuzzy_recovery) {
    lines.push('Fuzzy recovery: ' + (r.fuzzy_recovery.ok ? 'OK' : 'FAILED'));
    lines.push('Output source: ' + (r.output_source || 'unknown'));
  }
  lines.push('');
  lines.push('The redraw matched the enrolled drawing seed profile.');
  verifyOut(lines.join('\n'), 'ok');

  document.getElementById('unlockDomain').textContent = outputs.domain || r.domain || '—';
  document.getElementById('unlockPassword').textContent = outputs.demo_password || '—';
  document.getElementById('unlockSeed').textContent = outputs.seed_hex || '—';
  renderPalette('unlockPalette', outputs.avatar_palette);
  document.getElementById('vaultOutputs').classList.remove('hidden');

  setVaultPill('Access Granted', 'granted');
  document.getElementById('verifyCanvasWrap').classList.remove('denied');
  document.getElementById('verifyCanvasWrap').classList.add('granted');
  v.clear();
}

function renderUnlockFailure(r) {
  const lines = [];
  lines.push('ACCESS DENIED');
  lines.push('Final score: ' + Number(r.final_score ?? r.score ?? 0).toFixed(3));
  lines.push('Token score: ' + Number(r.token_score ?? r.score ?? 0).toFixed(3));
  if (r.geometry_scores) {
    lines.push('Layout score: ' + Number(r.geometry_scores.layout || 0).toFixed(3));
    lines.push('Relation score: ' + Number(r.geometry_scores.relation || 0).toFixed(3));
    lines.push('Curve score: ' + Number(r.geometry_scores.curve || 0).toFixed(3));
    lines.push('Shape score: ' + Number(r.geometry_scores.stroke_shape || 0).toFixed(3));
  }
  lines.push('Token threshold: ' + Number(r.threshold || 0).toFixed(3));
  lines.push('Profile: ' + r.profile);
  if (r.verification_id) lines.push('Logged verification ID: ' + r.verification_id);
  if (r.verification_log_error) lines.push('Verification log error: ' + r.verification_log_error);
  if (r.fuzzy_recovery) {
    lines.push('Fuzzy recovery: ' + (r.fuzzy_recovery.ok ? 'OK' : 'FAILED'));
    lines.push('Output source: ' + (r.output_source || 'none'));
  }
  if (r.failure_reasons && r.failure_reasons.length) {
    lines.push('Failure reasons: ' + r.failure_reasons.join(', '));
  }
  lines.push('');
  lines.push('Try redrawing the enrolled secret again.');
  verifyOut(lines.join('\n'), 'bad');

  document.getElementById('vaultOutputs').classList.add('hidden');
  setVaultPill('Access Denied', 'denied');

  const wrap = document.getElementById('verifyCanvasWrap');
  wrap.classList.remove('granted');
  wrap.classList.add('denied', 'shake');
  setTimeout(() => wrap.classList.remove('shake'), 450);
}

// Enrollment canvas controls

document.getElementById('undo').onclick = () => c.undo();
document.getElementById('clear').onclick = () => c.clear();

document.getElementById('reset').onclick = () => {
  attempts = [];
  analysisResult = null;
  verifyResult = null;
  c.clear();
  hideVaultSection();
  setSteps();
  out('Enrollment reset. Save 3 attempts, then analyze.');
  document.getElementById('jsonResult').textContent = '{}';
  document.getElementById('palette').innerHTML = '';
  localStorage.removeItem('drng_last_enrollment_result');
};

document.getElementById('saveAttempt').onclick = () => {
  if (c.get().length === 0) {
    out('Draw something first.', 'bad');
    return;
  }
  attempts.push({
    attempt_id: attempts.length + 1,
    strokes: c.get(),
    canvas_size: [canvas.width, canvas.height]
  });
  c.clear();
  setSteps();
  out('Attempt saved. ' + attempts.length + '/3 attempts ready.', attempts.length >= 3 ? 'ok' : '');
};

document.getElementById('analyze').onclick = async () => {
  try {
    if (attempts.length < 2) {
      out('Need at least 2 attempts; 3 is recommended.', 'bad');
      return;
    }
    analysisResult = await postJson('/api/analyze_enrollment', {
      attempts,
      domain: document.getElementById('domain').value,
      participant_id: pid.textContent,
      seed_label: document.getElementById('seedLabel').value,
      notes: document.getElementById('notes').value,
      ui_version: 'seed-enrollment-codefreeze'
    });
    renderResult(analysisResult);
    setSteps();
  } catch (e) {
    out(e.message, 'bad');
  }
};

document.getElementById('saveEnrollment').onclick = async () => {
  try {
    if (!document.getElementById('consent').checked) {
      out('Consent checkbox required.', 'bad');
      return;
    }
    if (!analysisResult) {
      analysisResult = await postJson('/api/analyze_enrollment', {
        attempts,
        domain: document.getElementById('domain').value,
        participant_id: pid.textContent,
        seed_label: document.getElementById('seedLabel').value,
        notes: document.getElementById('notes').value,
        ui_version: 'seed-enrollment-codefreeze'
      });
      renderResult(analysisResult);
    }
    const res = await postJson('/api/save_enrollment', {
      participant_id: pid.textContent,
      seed_label: document.getElementById('seedLabel').value,
      domain: document.getElementById('domain').value,
      notes: document.getElementById('notes').value,
      attempts,
      result: analysisResult,
      ui_version: 'seed-enrollment-phase2-vault-ui'
    });
    out('Enrollment saved. ' + JSON.stringify(res), 'ok');
  } catch (e) {
    out(e.message, 'bad');
  }
};

// Vault / verify-redraw controls

document.getElementById('verifyUndo').onclick = () => v.undo();
document.getElementById('verifyClear').onclick = () => {
  v.clear();
  document.getElementById('verifyCanvasWrap').classList.remove('granted', 'denied', 'shake');
  setVaultPill('Ready', 'neutral');
  verifyOut('Redraw the enrolled secret and click Unlock Vault.');
  document.getElementById('vaultOutputs').classList.add('hidden');
};

document.getElementById('unlockVault').onclick = async () => {
  try {
    const redrawStrokes = v.get();
    if (!analysisResult) {
      const saved = localStorage.getItem('drng_last_enrollment_result');
      if (saved) analysisResult = JSON.parse(saved);
    }
    if (!analysisResult) {
      verifyOut('No enrollment result available. Enroll a drawing seed first.', 'bad');
      return;
    }
    if (!analysisResult.accepted_for_demo) {
      verifyOut('This enrollment was not accepted for demo unlock. Try enrolling a stronger drawing.', 'bad');
      return;
    }
    if (!redrawStrokes.length) {
      verifyOut('Draw your secret again before unlocking.', 'bad');
      return;
    }

    setVaultPill('Checking…', 'neutral');
    verifyOut('Checking redraw against enrolled profile...');

    verifyResult = await postJson('/api/verify_redraw', {
      enrollment_result: analysisResult,
      enrollment_id: analysisResult.enrollment_id || (analysisResult.enrollment_saved && analysisResult.enrollment_saved.id),
      participant_id: pid.textContent,
      seed_label: document.getElementById('seedLabel').value,
      attempt_type: document.getElementById('attemptType').value,
      redraw_strokes: redrawStrokes,
      ui_version: 'seed-enrollment-codefreeze'
    });

    document.getElementById('verifyJson').textContent = JSON.stringify(verifyResult, null, 2);

    if (verifyResult.accepted) renderUnlockSuccess(verifyResult);
    else renderUnlockFailure(verifyResult);
  } catch (e) {
    verifyOut(e.message, 'bad');
    setVaultPill('Error', 'denied');
  }
};

setSteps();

// If the user refreshed after a successful enrollment, allow testing the last local enrollment.
try {
  const saved = localStorage.getItem('drng_last_enrollment_result');
  if (saved) {
    analysisResult = JSON.parse(saved);
    if (analysisResult && analysisResult.accepted_for_demo) {
      document.getElementById('jsonResult').textContent = JSON.stringify(analysisResult, null, 2);
      showVaultSection();
    }
  }
} catch (_) {}
