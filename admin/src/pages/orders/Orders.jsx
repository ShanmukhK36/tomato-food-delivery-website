import './Orders.css';
import axios from 'axios';
import { toast } from 'react-toastify';
import { useEffect, useState, useCallback } from 'react';
import { assets } from '../../../../frontend/src/assets/assets';

const Orders = ({ url }) => {
  const [orders, setOrders] = useState([]);
  const [loading, setLoading] = useState(false);
  const [updatingId, setUpdatingId] = useState(null);

  const fetchAllOrders = useCallback(async () => {
    try {
      setLoading(true);
      const { data } = await axios.get(`${url}/api/order/list`);
      if (data.success) {
        setOrders(data.orders || []);
      } else {
        toast.error(data.message || 'Failed to fetch orders');
      }
    } catch (err) {
      toast.error(err?.response?.data?.message || err.message || 'Error fetching orders');
    } finally {
      setLoading(false);
    }
  }, [url]);

  const statusHandler = async (event, orderId) => {
    const newStatus = event.target.value;
    try {
      setUpdatingId(orderId);
      const { data } = await axios.post(`${url}/api/order/status`, {
        orderId,
        status: newStatus,
      });
      if (data.success) {
        toast.success('Status updated');
        // refresh list to reflect backend truth
        await fetchAllOrders();
      } else {
        toast.error(data.message || 'Failed to update status');
      }
    } catch (err) {
      toast.error(err?.response?.data?.message || err.message || 'Error updating status');
    } finally {
      setUpdatingId(null);
    }
  };

  useEffect(() => {
    fetchAllOrders();
  }, [fetchAllOrders]);

  return (
    <div className="order add">
      <h3>Order Page</h3>

      {loading ? (
        <p>Loading orders…</p>
      ) : orders.length === 0 ? (
        <p>No paid orders found.</p>
      ) : (
        <div className="order-list">
          {orders.map((order) => (
            <div key={order._id} className="order-item">
              <img src={assets.parcel_icon} alt="Parcel" />
              <div>
                <p className="order-item-food">
                  {order.items.map((item, idx) => {
                    const isLast = idx === order.items.length - 1;
                    return (
                      <span key={`${order._id}-${item.name}-${idx}`}>
                        {item.name} x {item.quantity}
                        {!isLast && ', '}
                      </span>
                    );
                  })}
                </p>

                <p className="order-item-name">
                  {order.address.firstName} {order.address.lastName}
                </p>

                <div className="order-item-address">
                  <p>{order.address.street},</p>
                  <p>
                    {order.address.city}, {order.address.state}, {order.address.country},{' '}
                    {order.address.zipcode}
                  </p>
                </div>

                <p className="order-item-phone">{order.address.phone}</p>
              </div>

              <p>Items: {order.items.length}</p>
              <p>${Number(order.amount).toFixed(2)}</p>

              <select
                onChange={(e) => statusHandler(e, order._id)}
                value={order.status}
                disabled={updatingId === order._id}
              >
                <option value="Food Processing">Food Processing</option>
                <option value="Out For Delivery">Out For Delivery</option>
                <option value="Delivered">Delivered</option>
              </select>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default Orders;