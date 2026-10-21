/* Review outreach is manual: staff choose the timing and preview each email. */
'use strict';
const crmOldCare=X.pageRenderers.care;
X.pageRenderers.care=async()=>{const base=await crmOldCare();return base+panel('Review links & optional video','<p>Open the service or draft invoice and choose <strong>Client thank-you options</strong>. The crew or sales team can copy a review link or prepare a message for approval. No review request or video invitation is sent automatically.</p>');};
