import { useContext, useEffect, useState, useCallback } from 'react';
import { StoreContext } from '../context/StoreContext';
import axios from 'axios';
import { assets } from '../assets/assets';

const MyOrders = () => {
  const { url, token } = useContext(StoreContext);
  const [orders, setOrders] = useState([]);
  const [loading, setLoading] = useState(false);
  const [refreshingId, setRefreshingId] = useState(null);
  const [error, setError] = useState('');

  const fetchOrders = useCallback(async () => {
    if (!token) return;
    try {
      setLoading(true);
      setError('');
      const { data } = await axios.post(`${url}/api/order/userorders`, {}, { headers: { token } });
      if (data?.success) {
        setOrders(data.data || []);
      } else {
        setError(data?.message || 'Failed to load orders.');
      }
    } catch (err) {
      setError(err?.response?.data?.message || err.message || 'Failed to load orders.');
    } finally {
      setLoading(false);
    }
  }, [url, token]);

  const handleTrack = async (orderId) => {
    try {
      setRefreshingId(orderId);
      // For now, “track” just refreshes; replace with navigate(`/orders/${orderId}`) if you add a tracker page
      await fetchOrders();
    } finally {
      setRefreshingId(null);
    }
  };

  useEffect(() => {
    fetchOrders();
  }, [fetchOrders]);

  return (
    <div className="my-[50px] mx-[0px]">
      <h2 className="font-medium text-[#454545] text-[30px]">My Orders</h2>

      {loading ? (
        <p className="mt-[30px] text-[#454545]">Loading your orders…</p>
      ) : error ? (
        <p className="mt-[30px] text-red-600">{error}</p>
      ) : orders.length === 0 ? (
        <p className="mt-[30px] text-[#454545]">No orders yet.</p>
      ) : (
        <div className="flex flex-col gap-[20px] mt-[30px]">
          {orders.map((order) => {
            const itemsText = (order.items || [])
              .map((it) => `${it.name} x ${it.quantity}`)
              .join(', ');
            const amount = Number(order.amount || 0).toFixed(2);

            // status dot color
            const statusColor =
              order.status === 'Delivered'
                ? '#16a34a' // green
                : order.status === 'Out For Delivery'
                ? '#f59e0b' // amber
                : '#ea580c'; // orange (Food Processing / default)

            return (
              <div
                key={order._id}
                className="grid grid-cols-[0.5fr_2fr_1fr_1fr_2fr_1fr] items-center gap-[10px] md:gap-[30px] text-[14px] py-[10px] px-[10px] md:px-[20px] text-[#454545] border border-orange-600"
              >
                <img className="w-[50px]" src={assets.parcel_icon} alt="Order parcel" />

                <p className="truncate">{itemsText}</p>

                <p>${amount}</p>

                <p>Items: {order.items?.length || 0}</p>

                <p className="flex items-center gap-2">
                  <span className="text-orange-600" style={{ color: statusColor }}>
                    {'\u25cf'}
                  </span>
                  <b className="font-[500] text-[#454545]">{order.status}</b>
                </p>

                <button
                  onClick={() => handleTrack(order._id)}
                  disabled={refreshingId === order._id}
                  className="border-none py-[12px] px-[0px] rounded-[4px] bg-[#ffe1e1] hover:bg-[#f5c0c0] disabled:opacity-60 cursor-pointer text-[#454545]"
                  title="Refresh order status"
                >
                  {refreshingId === order._id ? 'Refreshing…' : 'Track Order'}
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default MyOrders;