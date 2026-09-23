import React, { useState } from 'react';
import { StepPips } from '../components/StepPips';
import type { Step } from '../components/StepPips';
import { AlertBanner } from '../components/AlertBanner';
import type { Alert } from '../components/AlertBanner';
import './CrewHUD.css';

const MOCK_STEPS: Step[] = [
  { id: 's1', name: 'Open outer container', state: 'complete' },
  { id: 's2', name: 'Remove red box', state: 'complete' },
  { id: 's3', name: 'Remove yellow box', state: 'complete' },
  { id: 's4', name: 'Open the red box', state: 'active' },
  { id: 's5', name: 'Transfer vial', state: 'pending' },
  { id: 's6', name: 'Close and secure', state: 'pending' },
];

export const CrewHUD: React.FC = () => {
  const [alert, setAlert] = useState<Alert | null>({
    id: 'a1',
    kind: 'skip',
    severity: 'high',
    message: 'Step 4 was not completed',
  });

  const activeStep = MOCK_STEPS.find(s => s.state === 'active');
  const activeIndex = MOCK_STEPS.findIndex(s => s.state === 'active');

  return (
    <div className="crew-hud">
      <AlertBanner alert={alert} onAcknowledge={() => setAlert(null)} />
      
      {!alert && (
        <div className="hud-main">
          <div className="hud-header">
            <span>PROC-A · Nested sample retrieval</span>
            <span className="live-dot">● LIVE</span>
          </div>

          <div className="hud-instruction-zone">
            {activeStep ? (
              <>
                <div className="hud-step-counter mono">STEP {activeIndex + 1} OF {MOCK_STEPS.length}</div>
                <div className="hud-instruction">{activeStep.name}</div>
                <div className="hud-progress-bar">
                  <div className="hud-progress-fill" style={{ width: '40%' }}></div>
                </div>
              </>
            ) : (
              <div className="hud-instruction">PROCEDURE COMPLETE</div>
            )}
          </div>
        </div>
      )}

      <div className="hud-footer">
        <StepPips steps={MOCK_STEPS} />
        
        <div className="hud-controls">
          <button className="hud-btn confirm">Confirm step</button>
          <button className="hud-btn override">Override</button>
          <button className="hud-btn mark">Mark anomaly</button>
        </div>
      </div>
    </div>
  );
};
