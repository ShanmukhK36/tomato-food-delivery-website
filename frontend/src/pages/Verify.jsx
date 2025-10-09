import { useContext, useEffect, useState, useCallback } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { StoreContext } from '../context/StoreContext';
import axios from 'axios';

const Verify = () => {
  const [searchParams] = useSearchParams();
  const orderId = searchParams.get('orderId');
  const sessionId = searchParams.get('session_id');
  const { url, setCartItems } = useContext(StoreContext);
  const navigate = useNavigate();
  const [message, setMessage] = useState('Confirming your payment…');

  const verifyPayment = useCallback(async () => {
    if (!orderId || !sessionId) {
      setMessage('Missing payment information. Redirecting…');
      setTimeout(() => navigate('/cart'), 1000);
      return;
    }

    try {
      const { data } = await axios.get(`${url}/api/order/verify`, {
        params: { orderId, session_id: sessionId },
      });

      if (data?.success) {
        // clear client-side cart for instant UX
        setCartItems({});
        setMessage('Payment successful! Redirecting…');
        setTimeout(() => navigate('/myorders'), 600);
      } else {
        // do NOT clear cart on failure
        setMessage('Payment not confirmed. Returning to cart…');
        setTimeout(() => navigate('/cart'), 1000);
      }
    } catch (err) {
      setMessage('Could not verify payment. Returning to cart…');
      setTimeout(() => navigate('/cart'), 1000);
    }
  }, [orderId, sessionId, url, navigate, setCartItems]);

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