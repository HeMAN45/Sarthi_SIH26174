import React from 'react';
import './StepPips.css';

export interface Step {
  id: string;
  name: string;
  state: 'pending' | 'active' | 'complete' | 'skipped' | 'out_of_order' | 'unverified' | 'overridden' | 'stalled';
}

interface StepPipsProps {
  steps: Step[];
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

const getClassName = (state: Step['state']) => {
  return `pip-item state-${state}`;
};

export const StepPips: React.FC<StepPipsProps> = ({ steps }) => {
  return (
    <div className="step-pips-container">
      {steps.map((step, index) => (
        <div key={step.id} className={getClassName(step.state)} title={step.name}>
          <span className="pip-glyph">{getGlyph(step.state)}</span>
          <span className="pip-num">{index + 1}</span>
        </div>
      ))}
    </div>
  );
};
