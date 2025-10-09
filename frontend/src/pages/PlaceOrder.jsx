import { useContext, useEffect, useMemo, useState } from 'react';
import { StoreContext } from '../context/StoreContext';
import axios from 'axios';
import { useNavigate } from 'react-router-dom';

const DELIVERY_FEE = 2;

const PlaceOrder = () => {
  const navigate = useNavigate();
  const { getTotalCartValue, cartItems, token, food_list, url, user } = useContext(StoreContext);
  // ^ ensure StoreContext provides `user` (with ._id or .id). If not, pass userId some other way.

  const [data, setData] = useState({
    firstName: '',
    lastName: '',      
    email: '',
    street: '',
    city: '',
    state: '',
    zipcode: '',
    country: '',
    phone: ''
  });

  const [submitting, setSubmitting] = useState(false);

  const onChangeHandler = (e) => {
    const { name, value } = e.target;
    setData((prev) => ({ ...prev, [name]: value }));
  };

  // Build order items from cart safely (no mutation)
  const orderItems = useMemo(() => {
    const items = [];
    if (!food_list) return items;
    food_list.forEach((item) => {
      const qty = cartItems?.[item._id] || 0;
      if (qty > 0) {
        items.push({ ...item, quantity: qty }); // ✅ clone + add quantity
      }
    });
    return items;
  }, [food_list, cartItems]);

  const subtotal = useMemo(() => Number(getTotalCartValue() || 0), [getTotalCartValue]);
  const total = useMemo(() => subtotal + DELIVERY_FEE, [subtotal]);

  const placeOrder = async (e) => {
    e.preventDefault();
    if (!orderItems.length) return;

    try {
      setSubmitting(true);

      const orderData = {
        userId: user?._id || user?.id, 
        address: data,
        items: orderItems,
        amount: total
      };

      const response = await axios.post(`${url}/api/order/place`, orderData, {
        headers: { token }
      });

      if (response.data?.success) {
        const { session_url } = response.data;
        window.location.replace(session_url);
      } else {
        alert(response.data?.message || 'Failed to create checkout session.');
      }
    } catch (err) {
      alert(err?.response?.data?.message || err.message || 'Something went wrong.');
    } finally {
      setSubmitting(false);
    }
  };

  useEffect(() => {
    // Guard: require auth and non-empty cart
    if (!token || subtotal === 0) {
      navigate('/cart');
    }
  }, [token, subtotal, navigate]);

  return (
    <form onSubmit={placeOrder} className="flex flex-col md:flex-row items-start justify-between gap-[50px] mt-[100px]">
      {/* Left View */}
      <div className="w-full sm:w-[40%]">
        <p className="text-[30px] font-[600] mb-[50px]">Delivery Information</p>

        <div className="flex gap-[10px]">
          <input
            required
            name="firstName"
            onChange={onChangeHandler}
            className="w-full mb-[15px] p-[10px] border-[1px] border-[#c5c5c5] rounded-[4px] outline-orange-600"
            type="text"
            placeholder="First Name"
            autoComplete="given-name"
          />
          <input
            required
            name="lastName"
            onChange={onChangeHandler}
            className="w-full mb-[15px] p-[10px] border-[1px] border-[#c5c5c5] rounded-[4px] outline-orange-600"
            type="text"
            placeholder="Last Name"
            autoComplete="family-name"
          />
        </div>

        <input
          required
          name="email"
          onChange={onChangeHandler}
          className="w-full mb-[15px] p-[10px] border-[1px] border-[#c5c5c5] rounded-[4px] outline-orange-600"
          type="email"
          placeholder="Email"
          autoComplete="email"
        />
        <input
          required
          name="street"
          onChange={onChangeHandler}
          className="w-full mb-[15px] p-[10px] border-[1px] border-[#c5c5c5] rounded-[4px] outline-orange-600"
          type="text"
          placeholder="Street"
          autoComplete="address-line1"
        />

        <div className="flex gap-[10px]">
          <input
            required
            name="city"
            onChange={onChangeHandler}
            className="w-full mb-[15px] p-[10px] border-[1px] border-[#c5c5c5] rounded-[4px] outline-orange-600"
            type="text"
            placeholder="City"
            autoComplete="address-level2"
          />
          <input
            required
            name="state"
            onChange={onChangeHandler}
            className="w-full mb-[15px] p-[10px] border-[1px] border-[#c5c5c5] rounded-[4px] outline-orange-600"
            type="text"
            placeholder="State"
            autoComplete="address-level1"
          />
        </div>

        <div className="flex gap-[10px]">
          <input
            required
            name="zipcode"
            onChange={onChangeHandler}
            className="w-full mb-[15px] p-[10px] border-[1px] border-[#c5c5c5] rounded-[4px] outline-orange-600"
            type="text"
            placeholder="Zipcode"
            autoComplete="postal-code"
          />
          <input
            required
            name="country"
            onChange={onChangeHandler}
            className="w-full mb-[15px] p-[10px] border-[1px] border-[#c5c5c5] rounded-[4px] outline-orange-600"
            type="text"
            placeholder="Country"
            autoComplete="country-name"
          />
        </div>

        <input
          required
          name="phone"
          onChange={onChangeHandler}
          className="w-full mb-[15px] p-[10px] border-[1px] border-[#c5c5c5] rounded-[4px] outline-orange-600"
          type="tel"
          placeholder="Phone"
          autoComplete="tel"
        />
      </div>

      {/* Right View */}
      <div className="w-full sm:w-[40%]">
        <div className="flex-1 flex flex-col gap-[20px]">
          <h2 className="text-[30px] font-[600] mb-[30px]">CART TOTAL</h2>
          <div>
            <div className="flex justify-between text-[#555]">
              <p>Subtotal</p>
              <p>${subtotal.toFixed(2)}</p>
            </div>
            <hr className="h-[1px] border-[#e2e2e2] border my-[10px] mx-[0px]" />
            <div className="flex justify-between text-[#555]">
              <p>Delivery Fee</p>
              <p>${DELIVERY_FEE.toFixed(2)}</p>
            </div>
            <hr className="h-[1px] border-[#e2e2e2] border my-[10px] mx-[0px]" />
            <div className="flex justify-between text-[#555]">
              <b>Total</b>
              <b>${total.toFixed(2)}</b>
            </div>
          </div>

          <button
            type="submit"
            disabled={submitting || subtotal === 0}
            className="border border-gray-600 rounded-[4px] text-white bg-orange-600 hover:bg-orange-700 disabled:bg-gray-600 w-[200px] py-[12px] px-[0px] cursor-pointer"
          >
            {submitting ? 'Redirecting…' : 'Proceed To Payment'}
          </button>
        </div>
      </div>
    </form>
  );
};

export default PlaceOrder;