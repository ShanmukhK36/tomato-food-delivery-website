import { useContext, useEffect, useState, useCallback } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { StoreContext } from '../context/StoreContext';
import '../index.css';
import axios from 'axios';

const Verify = () => {
  const [searchParams] = useSearchParams();
  const orderId = searchParams.get('orderId');
  const sessionId = searchParams.get('session_id'); // ✅ Stripe adds this in success_url/cancel_url
  const { url } = useContext(StoreContext);
  const navigate = useNavigate();
  const [message, setMessage] = useState('Confirming your payment…');

  const verifyPayment = useCallback(async () => {
    // If we somehow didn't get the session_id, just send the user to orders
    if (!orderId || !sessionId) {
      setMessage('Missing payment information. Redirecting…');
      setTimeout(() => navigate('/myorders'), 1200);
      return;
    }

    try {
      // ✅ Backend verify is a GET that reads req.query (orderId & session_id)
      const { data } = await axios.get(
        `${url}/api/order/verify`,
        { params: { orderId, session_id: sessionId } }
      );

      if (data?.success) {
        // Success is persisted immediately
        setMessage('Payment successful! Redirecting to your orders…');
        setTimeout(() => navigate('/myorders'), 800);
      } else {
        // Not yet paid or canceled — DO NOT persist failure here.
        // Webhook will update the DB authoritatively if/when Stripe completes.
        setMessage('Waiting for Stripe confirmation…');
        // Give the webhook a moment, then send the user to My Orders
        setTimeout(() => navigate('/myorders'), 1500);
      }
    } catch (err) {
      setMessage('Could not verify payment. Redirecting…');
      setTimeout(() => navigate('/myorders'), 1200);
    }
  }, [orderId, sessionId, url, navigate]);

  useEffect(() => {
    verifyPayment();
  }, [verifyPayment]);

  return (
    <div className="h-[60vh] grid place-items-center text-[#454545]">
      <div className="flex flex-col items-center gap-3">
        <div className="spinner w-[100px] h-[100px] rounded-full border-[5px] border-[#bdbdbd] border-t-orange-600" />
        <p className="text-sm">{message}</p>
      </div>
    </div>
  );
};

export default Verify;