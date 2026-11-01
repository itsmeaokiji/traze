import { formatNumber } from './format';
import type { TransactionCost } from './types';

const rateFormat = new Intl.NumberFormat('en-US', { maximumFractionDigits: 4 });
const reasons: Record<string, string> = {
  previous_outputs_unavailable: 'Required previous-output data is unavailable in the retained transaction records.',
  previous_output_records_unavailable: 'Retained previous-output records could not be read.',
  transaction_data_invalid: 'The supplied transaction or previous-output data failed cost checks.',
  cost_read_limit: 'Cost calculation reached its local read limit.',
  coinbase_fee_not_applicable: 'A transaction fee does not apply to a coinbase transaction.',
};

export function TransactionCosts({ value, headingLevel = 4 }: { value?: TransactionCost; headingLevel?: 2 | 4 }) {
  if (!value) return null;
  const calculated = value.status === 'calculated' && typeof value.fee_sats === 'number'
    && value.fee_sats >= 0 && typeof value.vsize_vbytes === 'number' && value.vsize_vbytes > 0;
  const rate = calculated ? value.fee_sats! / value.vsize_vbytes! : undefined;
  const rateLabel = rate !== undefined ? rate > 0 && rate < .0001 ? '<0.0001' : rateFormat.format(rate) : undefined;
  const Heading = headingLevel === 2 ? 'h2' : 'h4';
  return <section className="transaction-costs" aria-label="Transaction costs">
    <Heading className="detail-label">Transaction costs</Heading>
    <dl className="inspector-facts">
      <div className="inspector-fact"><dt>Transaction fee</dt><dd>{calculated ? `${formatNumber(value.fee_sats)} sats` : 'Unavailable'}</dd></div>
      <div className="inspector-fact"><dt>Virtual size</dt><dd>{typeof value.vsize_vbytes === 'number' ? `${formatNumber(value.vsize_vbytes)} vB` : 'Unavailable'}</dd></div>
      <div className="inspector-fact"><dt>Fee rate</dt><dd>{calculated ? `${rateLabel} sat/vB` : 'Unavailable'}</dd></div>
    </dl>
    <p className="field-help">{calculated ? 'Calculated from retained transaction bytes and checked previous-output references. ' : `${reasons[value.reason || ''] || 'Transaction-cost data is unavailable.'} `}
      Costs apply to the whole Bitcoin transaction, including any other publication outputs. They do not establish PoP rewards.</p>
    {value.segwit && <p className="field-help">Size and fee rate use the supplied witness bytes. Bitcoin witness-commitment verification is outside these checks.</p>}
  </section>;
}
