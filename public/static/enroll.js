const canvas = document.getElementById('canvas');
const c = setupCanvas(canvas, document.getElementById('count'));

const verifyCanvas = document.getElementById('verifyCanvas');
const v = setupCanvas(verifyCanvas, document.getElementById('verifyCount'));

const stepCanvas = document.getElementById('stepCanvas');
const step = stepCanvas ? setupCanvas(stepCanvas, document.getElementById('stepCount')) : null;

const pid = document.getElementById('pid');
pid.textContent = makeParticipantId();
document.getElementById('newPid').onclick = () => pid.textContent = newParticipantId();

let attempts = [];
let analysisResult = null;
let verifyResult = null;
let pendingStepUp = null;
let pendingFullRedrawStrokes = null;

function setSteps() {
  for (let i = 1; i <= 4; i++) {
    const step = document.getElementById('s' + i);
    step.classList.remove('active', 'complete');
    if (i <= attempts.length) step.classList.add('complete');
  }
  const activeStep = analysisResult ? 4 : Math.min(attempts.length + 1, 4);
  document.getElementById('s' + activeStep).classList.add('active');
  document.getElementById('attemptCount').textContent = attempts.length;
  document.getElementById('currentTask').textContent = attempts.length < 3
    ? `Draw attempt ${attempts.length + 1} of 3`
    : 'All attempts saved. Analyze when ready.';
  document.getElementById('saveAttempt').textContent = attempts.length < 3
    ? `Save attempt ${attempts.length + 1}`
    : 'Three attempts saved';
  document.getElementById('saveAttempt').disabled = attempts.length >= 3;
  document.getElementById('analyze').disabled = attempts.length < 3;
  { const saveBtn = document.getElementById('saveEnrollment'); if (saveBtn) saveBtn.disabled = !analysisResult; }
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
  verifyOut('Redraw the enrolled seed and click Check Redraw.');
}

function hideVaultSection() {
  document.getElementById('vaultSection').classList.add('hidden');
  document.getElementById('vaultOutputs').classList.add('hidden');
  verifyResult = null;
  pendingStepUp = null;
  pendingFullRedrawStrokes = null;
  v.clear();
  if (step) step.clear();
  const su = document.getElementById('stepUpSection');
  if (su) su.classList.add('hidden');
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
  if (r.seed_quality) {
    lines.push('Seed quality: ' + Number(r.seed_quality.quality_score || 0).toFixed(1) + ' / 100 (' + r.seed_quality.quality_label + ')');
    if (r.seed_quality.hard_reject) {
      lines.push('Quality reject reasons: ' + ((r.seed_quality.hard_reject_reasons || []).join(', ') || 'none'));
    }
    if (r.seed_quality.feature_breakdown) {
      const q = r.seed_quality.feature_breakdown;
      lines.push('Quality breakdown: stability ' + Number(q.stability || 0).toFixed(2) +
        ', token ' + Number(q.token_complexity || 0).toFixed(2) +
        ', geometry ' + Number(q.geometry_complexity || 0).toFixed(2) +
        ', common-risk ' + Number(q.common_shape_risk || 0).toFixed(2));
    }
    if ((r.seed_quality.recommendations || []).length) {
      lines.push('Quality recommendations: ' + r.seed_quality.recommendations.join(' | '));
    }
  }
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


function setStepPill(text, cls = 'neutral') {
  const pill = document.getElementById('stepUpStatusPill');
  if (!pill) return;
  pill.textContent = text;
  pill.className = 'status-pill ' + cls;
}

function stepOut(text, cls = '') {
  const el = document.getElementById('stepUpResult');
  if (!el) return;
  el.textContent = text;
  el.className = 'output ' + cls;
}

function hideStepUp() {
  pendingStepUp = null;
  pendingFullRedrawStrokes = null;
  const box = document.getElementById('stepUpSection');
  if (box) box.classList.add('hidden');
  if (step) step.clear();
}

function renderStepUpRequired(r) {
  pendingStepUp = r.step_up_challenge;
  const box = document.getElementById('stepUpSection');
  if (!box || !pendingStepUp) return;
  box.classList.remove('hidden');
  document.getElementById('stepUpPrompt').textContent = pendingStepUp.prompt || 'Redraw only the requested remembered component.';
  setStepPill('Required', 'neutral');
  const lines = [];
  lines.push('STEP-UP REQUIRED');
  lines.push(pendingStepUp.prompt || 'Redraw the requested component.');
  lines.push('Reason: ' + (pendingStepUp.trigger || pendingStepUp.reason || 'borderline_or_suspicious_match'));
  lines.push('Full redraw final score: ' + Number(r.final_score ?? r.score ?? 0).toFixed(3));
  if (r.timing_scores && r.timing_scores.timing_final !== null && r.timing_scores.timing_final !== undefined) {
    lines.push('Timing/rhythm score: ' + Number(r.timing_scores.timing_final || 0).toFixed(3) + ' (diagnostic)');
  }
  lines.push('No password or seed is revealed until this component challenge passes.');
  verifyOut(lines.join('\n'), 'warn');
  document.getElementById('vaultOutputs').classList.add('hidden');
  setVaultPill('Step-up Required', 'neutral');
  refreshUseCaseSimulations(r);
  stepOut('Draw only the requested component, then submit.', 'warn');
}

function renderUnlockSuccess(r) {
  hideStepUp();
  const outputs = r.outputs || {};
  const lines = [];
  lines.push('ACCESS GRANTED');
  lines.push('Final score: ' + Number(r.final_score ?? r.score ?? 0).toFixed(3));
  if (r.high_confidence_override) lines.push('Decision: accepted by high-confidence override (> 0.800)');
  lines.push('Token score: ' + Number(r.token_score ?? r.score ?? 0).toFixed(3));
  if (r.complex_scene_mode) {
    lines.push('Mode: complex scene');
    if (r.complex_token_threshold !== null && r.complex_token_threshold !== undefined) {
      lines.push('Complex token threshold: ' + Number(r.complex_token_threshold || 0).toFixed(3));
    }
    if (r.complex_owner_recovery_pass) lines.push('Decision note: complex owner-recovery band used');
    if (r.scene_scores) {
      lines.push('Scene final: ' + Number(r.scene_scores.scene_final || 0).toFixed(3));
      lines.push('Scene assignment: ' + Number(r.scene_scores.scene_assignment || 0).toFixed(3));
      lines.push('Scene raster: ' + Number(r.scene_scores.scene_raster || 0).toFixed(3));
      lines.push('Scene relation: ' + Number(r.scene_scores.scene_relation || 0).toFixed(3));
    }
  }
  if (r.geometry_scores) {
    lines.push('Layout score: ' + Number(r.geometry_scores.layout || 0).toFixed(3));
    lines.push('Relation score: ' + Number(r.geometry_scores.relation || 0).toFixed(3));
    if (r.geometry_scores.topology !== undefined) {
      lines.push('Topology score: ' + Number(r.geometry_scores.topology || 0).toFixed(3));
    }
    if (r.geometry_scores.topology_flags && r.geometry_scores.topology_flags.length) {
      lines.push('Topology notes: ' + r.geometry_scores.topology_flags.join(', '));
    }
    lines.push('Curve score: ' + Number(r.geometry_scores.curve || 0).toFixed(3));
    lines.push('Shape score: ' + Number(r.geometry_scores.stroke_shape || 0).toFixed(3));
    if (r.geometry_scores.closed_style_applicable === false) {
      lines.push('Closed style score: not applicable');
    } else if (r.geometry_scores.closed_style !== undefined) {
      lines.push('Closed style score: ' + Number(r.geometry_scores.closed_style || 0).toFixed(3));
    }
  }
  lines.push('Token threshold: ' + Number(r.threshold || 0).toFixed(3));
  lines.push('Profile: ' + r.profile);
  if (r.verification_id) lines.push('Logged verification ID: ' + r.verification_id);
  if (r.verification_log_error) lines.push('Verification log error: ' + r.verification_log_error);
  if (r.fuzzy_recovery) {
    lines.push('Fuzzy recovery: ' + (r.fuzzy_recovery.ok ? 'OK' : 'FAILED'));
    lines.push('Output source: ' + (r.output_source || 'unknown'));
  }
  if (r.timing_scores && r.timing_scores.timing_final !== null && r.timing_scores.timing_final !== undefined) {
    lines.push('Timing/rhythm score: ' + Number(r.timing_scores.timing_final || 0).toFixed(3) + ' (diagnostic)');
  }
  if (r.step_up_challenge) {
    lines.push('Step-up challenge suggested: ' + r.step_up_challenge.prompt);
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
  refreshUseCaseSimulations(r);
  document.getElementById('verifyCanvasWrap').classList.remove('denied');
  document.getElementById('verifyCanvasWrap').classList.add('granted');
  v.clear();
}

function renderUnlockFailure(r) {
  const lines = [];
  lines.push('ACCESS DENIED');
  lines.push('Final score: ' + Number(r.final_score ?? r.score ?? 0).toFixed(3));
  if (r.high_confidence_override) lines.push('Decision: accepted by high-confidence override (> 0.800)');
  lines.push('Token score: ' + Number(r.token_score ?? r.score ?? 0).toFixed(3));
  if (r.complex_scene_mode) {
    lines.push('Mode: complex scene');
    if (r.complex_token_threshold !== null && r.complex_token_threshold !== undefined) {
      lines.push('Complex token threshold: ' + Number(r.complex_token_threshold || 0).toFixed(3));
    }
    if (r.complex_owner_recovery_pass) lines.push('Decision note: complex owner-recovery band used');
    if (r.scene_scores) {
      lines.push('Scene final: ' + Number(r.scene_scores.scene_final || 0).toFixed(3));
      lines.push('Scene assignment: ' + Number(r.scene_scores.scene_assignment || 0).toFixed(3));
      lines.push('Scene raster: ' + Number(r.scene_scores.scene_raster || 0).toFixed(3));
      lines.push('Scene relation: ' + Number(r.scene_scores.scene_relation || 0).toFixed(3));
    }
  }
  if (r.geometry_scores) {
    lines.push('Layout score: ' + Number(r.geometry_scores.layout || 0).toFixed(3));
    lines.push('Relation score: ' + Number(r.geometry_scores.relation || 0).toFixed(3));
    if (r.geometry_scores.topology !== undefined) {
      lines.push('Topology score: ' + Number(r.geometry_scores.topology || 0).toFixed(3));
    }
    if (r.geometry_scores.topology_flags && r.geometry_scores.topology_flags.length) {
      lines.push('Topology notes: ' + r.geometry_scores.topology_flags.join(', '));
    }
    lines.push('Curve score: ' + Number(r.geometry_scores.curve || 0).toFixed(3));
    lines.push('Shape score: ' + Number(r.geometry_scores.stroke_shape || 0).toFixed(3));
    if (r.geometry_scores.closed_style_applicable === false) {
      lines.push('Closed style score: not applicable');
    } else if (r.geometry_scores.closed_style !== undefined) {
      lines.push('Closed style score: ' + Number(r.geometry_scores.closed_style || 0).toFixed(3));
    }
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
  if (r.step_up_challenge) {
    lines.push('Borderline case: ' + r.step_up_challenge.prompt);
  }
  lines.push('');
  lines.push('Try redrawing the enrolled secret again.');
  verifyOut(lines.join('\n'), 'bad');

  document.getElementById('vaultOutputs').classList.add('hidden');
  setVaultPill('Access Denied', 'denied');
  refreshUseCaseSimulations(r);

  const wrap = document.getElementById('verifyCanvasWrap');
  wrap.classList.remove('granted');
  wrap.classList.add('denied', 'shake');
  setTimeout(() => wrap.classList.remove('shake'), 450);
}


function setUseCasePill(text, cls = 'neutral') {
  const pill = document.getElementById('useCaseStatusPill');
  if (!pill) return;
  pill.textContent = text;
  pill.className = 'status-pill ' + cls;
}

function useCaseStateClass(state) {
  if (state === 'granted') return 'granted';
  if (state === 'step_up') return 'neutral';
  return 'denied';
}

function renderUseCaseSimulations(payload) {
  const section = document.getElementById('useCaseSection');
  const grid = document.getElementById('useCaseGrid');
  const summary = document.getElementById('useCaseSummary');
  const json = document.getElementById('useCaseJson');
  if (!section || !grid) return;
  section.classList.remove('hidden');
  grid.innerHTML = '';
  json.textContent = JSON.stringify(payload, null, 2);
  const sims = payload.simulations || [];
  const counts = (payload.summary && payload.summary.states) || {};
  summary.textContent = (payload.summary && payload.summary.takeaway) || 'Use-case simulations generated from the latest verification result.';
  setUseCasePill(`${counts.granted || 0} grant · ${counts.step_up || 0} step-up · ${counts.denied || 0} deny`, 'neutral');

  for (const sim of sims) {
    const card = document.createElement('article');
    card.className = 'usecase-card ' + useCaseStateClass(sim.state);

    const top = document.createElement('div');
    top.className = 'usecase-topline';
    const title = document.createElement('div');
    title.innerHTML = `<p class="eyebrow">${(sim.state || 'unknown').replace('_', ' ')}</p><h3>${sim.title || sim.id}</h3>`;
    const pill = document.createElement('span');
    pill.className = 'status-pill ' + useCaseStateClass(sim.state);
    pill.textContent = sim.outcome || sim.state || 'unknown';
    top.appendChild(title);
    top.appendChild(pill);
    card.appendChild(top);

    const subtitle = document.createElement('p');
    subtitle.className = 'muted small';
    subtitle.textContent = sim.subtitle || '';
    card.appendChild(subtitle);

    const action = document.createElement('p');
    action.className = 'usecase-action';
    action.textContent = sim.action || '';
    card.appendChild(action);

    const note = document.createElement('p');
    note.className = 'tiny';
    note.textContent = sim.security_note || '';
    card.appendChild(note);

    const metrics = sim.metrics || {};
    const metricLine = document.createElement('div');
    metricLine.className = 'usecase-metrics';
    const compact = [
      ['final', metrics.final_score],
      ['token', metrics.token_score],
      ['scene', metrics.scene_score],
      ['timing', metrics.timing_score],
      ['quality', metrics.seed_quality]
    ].filter(x => x[1] !== null && x[1] !== undefined && !Number.isNaN(x[1]));
    metricLine.innerHTML = compact.map(([k, v]) => `<span><b>${k}</b> ${v}</span>`).join('');
    card.appendChild(metricLine);

    if (sim.display_output && (sim.display_output.demo_password || sim.display_output.seed_hex_preview)) {
      const output = document.createElement('div');
      output.className = 'usecase-output';
      output.innerHTML = `<span>Demo output</span><code>${sim.display_output.domain || 'example.com'} → ${sim.display_output.demo_password || 'not released'}</code><code>seed ${sim.display_output.seed_hex_preview || 'not released'}</code>`;
      card.appendChild(output);
    }

    grid.appendChild(card);
  }
}

async function refreshUseCaseSimulations(result) {
  const section = document.getElementById('useCaseSection');
  if (!section || !result) return;
  try {
    setUseCasePill('Simulating…', 'neutral');
    const payload = await postJson('/api/simulate_use_cases', {
      enrollment_result: analysisResult,
      verification_result: result,
      domain: document.getElementById('domain').value,
      use_cases: ['password_manager', 'domain_password', 'captcha_like', 'account_recovery', 'informed_forgery']
    });
    renderUseCaseSimulations(payload);
  } catch (e) {
    section.classList.remove('hidden');
    setUseCasePill('Simulation error', 'denied');
    const summary = document.getElementById('useCaseSummary');
    if (summary) summary.textContent = e.message;
  }
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
  if (attempts.length >= 3) return;
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
    if (attempts.length < 3) {
      out('Save all three attempts before analyzing.', 'bad');
      return;
    }
    const analyzeButton = document.getElementById('analyze');
    analyzeButton.disabled = true;
    analyzeButton.textContent = 'Analyzing...';
    analysisResult = await postJson('/api/analyze_enrollment', {
      attempts,
      domain: document.getElementById('domain').value,
      participant_id: pid.textContent,
      seed_label: document.getElementById('seedLabel').value,
      notes: document.getElementById('notes').value,
      ui_version: 'public-demo-autolog-phase4-3'
    });
    renderResult(analysisResult);
    setSteps();
  } catch (e) {
    out(e.message, 'bad');
  } finally {
    document.getElementById('analyze').textContent = 'Analyze drawings';
    setSteps();
  }
};

{ const saveEnrollmentBtn = document.getElementById('saveEnrollment'); if (saveEnrollmentBtn) saveEnrollmentBtn.onclick = async () => {
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
        ui_version: 'public-demo-autolog-phase4-3'
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
      ui_version: 'public-demo-manual-save-phase4-3'
    });
    out(res.storage === 'disabled' ? 'Server logging disabled in the public demo. Enrollment remains available in this browser session.' : 'Enrollment saved. ' + JSON.stringify(res), 'ok');
  } catch (e) {
    out(e.message, 'bad');
  }
}; }

// Vault / verify-redraw controls

document.getElementById('verifyUndo').onclick = () => v.undo();
document.getElementById('verifyClear').onclick = () => {
  v.clear();
  document.getElementById('verifyCanvasWrap').classList.remove('granted', 'denied', 'shake');
  setVaultPill('Ready', 'neutral');
  verifyOut('Redraw the enrolled seed and click Check Redraw.');
  document.getElementById('vaultOutputs').classList.add('hidden');
  hideStepUp();
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
      verifyOut('Draw the enrolled seed again before checking.', 'bad');
      return;
    }

    setVaultPill('Checking…', 'neutral');
    verifyOut('Checking redraw against enrolled profile and logging this attempt for the demo dataset...');

    verifyResult = await postJson('/api/verify_redraw', {
      enrollment_result: analysisResult,
      enrollment_id: analysisResult.enrollment_id || (analysisResult.enrollment_saved && analysisResult.enrollment_saved.id),
      participant_id: pid.textContent,
      seed_label: document.getElementById('seedLabel').value,
      attempt_type: (document.getElementById('attemptType') ? document.getElementById('attemptType').value : 'owner_test'),
      redraw_strokes: redrawStrokes,
      ui_version: 'public-demo-autolog-phase4-3'
    });

    document.getElementById('verifyJson').textContent = JSON.stringify(verifyResult, null, 2);

    pendingFullRedrawStrokes = redrawStrokes;
    if (verifyResult.step_up_required) renderStepUpRequired(verifyResult);
    else if (verifyResult.accepted) renderUnlockSuccess(verifyResult);
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
