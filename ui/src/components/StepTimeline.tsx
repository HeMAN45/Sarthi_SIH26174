import React from 'react';
import type { Step } from './StepPips';
import './StepTimeline.css';

interface TimelineStep extends Step {
  duration_s?: number;
}

interface StepTimelineProps {
  steps: TimelineStep[];
}

const getGlyph = (state: Step['state']) => {
  switch (state) {
    case 'pending': return '○';
    case 'active': return '●';
    case 'complete': return '✓';
    case 'skipped': return '✕';
    case 'out_of_order': return '⇄';
    case 'unverified': return '?';
    case 'stalled': return '⏱';
    case 'overridden': return '⊙';
    default: return '○';
  }
};

export const StepTimeline: React.FC<StepTimelineProps> = ({ steps }) => {
  return (
    <div className="step-timeline panel">
      <div className="timeline-header">PROCEDURE</div>
      <div className="timeline-list">
        {steps.map((step, index) => (
          <div key={step.id} className={`timeline-item state-${step.state}`}>
            <span className="timeline-glyph">{getGlyph(step.state)}</span>
            <span className="timeline-num mono">{index + 1}</span>
            <span className="timeline-name">{step.name}</span>
            <span className="timeline-duration mono">
              {step.duration_s !== undefined ? `${step.duration_s.toFixed(1)}s` : ''}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
};
