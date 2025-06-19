import { useContext, useEffect, useState } from 'react';
import { StoreContext } from '../context/StoreContext';
import axios from 'axios';
import { useNavigate } from 'react-router-dom';

const PlaceOrder = () => {
    const navigate = useNavigate();
    const {getTotalCartValue, cartItems, token, food_list, url} = useContext(StoreContext);
    const [data, setData] = useState({
        firstName: '',
        last_Name: '',
        email: '',
        street: '',
        city: '',
        state: '',
        zipcode: '',
        country: '',
        phone: ''
    });
    const onChangeHandler = (event) => {
        const name = event.target.name;
        const value = event.target.value;
        setData(data => ({...data, [name]: value}));
    }
    const placeOrder = async (event) => {
        event.preventDefault();
        let orderItems = [];
        food_list.map((item) => {
            if(cartItems[item._id] > 0) {
                let itemInfo = item;
                itemInfo['quantity'] = cartItems[item._id];
                orderItems.push(itemInfo);
            }
        })
        let orderData = {
            address: data,
            items: orderItems,
            amount: getTotalCartValue() + 2
        }
        let response = await axios.post(url+'/api/order/place', orderData, {headers: {token}});
        if(response.data.success) {
            const {session_url} = response.data;
            window.location.replace(session_url);
        } else {
            alert('Error ')
        }
    }
    useEffect(() => {
        if(!token) {
            navigate('/cart');
        } else if(getTotalCartValue() === 0) {
            navigate('/cart');
        }
    }, [token])
  return (
    <form onSubmit={placeOrder} className='flex flex-col md:flex-row items-start justify-between gap-[50px] mt-[100px]'>
        {/* Left View */}
        <div className='w-full sm:w-[40%]'>
            <p className='text-[30px] font-[600] mb-[50px]'>Delivery Information</p>
            <div className='flex gap-[10px]'>
                <input required name='firstName' onChange={onChangeHandler} className='w-full mb-[15px] p-[10px] border-[1px] border-[#c5c5c5] rounded-[4px] outline-orange-600' type='text' placeholder='First Name'/>
                <input required name='lastName' onChange={onChangeHandler} className='w-full mb-[15px] p-[10px] border-[1px] border-[#c5c5c5] rounded-[4px] outline-orange-600' type='text' placeholder='Last Name Name'/>
            </div>
            <input required name='email' onChange={onChangeHandler} className='w-full mb-[15px] p-[10px] border-[1px] border-[#c5c5c5] rounded-[4px] outline-orange-600' type='text' placeholder='Email'/>
            <input required name='street' onChange={onChangeHandler} className='w-full mb-[15px] p-[10px] border-[1px] border-[#c5c5c5] rounded-[4px] outline-orange-600' type='text' placeholder='Street'/>
            <div className='flex gap-[10px]'>
                <input required name='city' onChange={onChangeHandler} className='w-full mb-[15px] p-[10px] border-[1px] border-[#c5c5c5] rounded-[4px] outline-orange-600' type='text' placeholder='City'/>
                <input required name='state' onChange={onChangeHandler} className='w-full mb-[15px] p-[10px] border-[1px] border-[#c5c5c5] rounded-[4px] outline-orange-600' type='text' placeholder='State'/>
            </div>
            <div className='flex gap-[10px]'>
                <input required name='zipcode' onChange={onChangeHandler} className='w-full mb-[15px] p-[10px] border-[1px] border-[#c5c5c5] rounded-[4px] outline-orange-600' type='text' placeholder='Zipcode'/>
                <input required name='country' onChange={onChangeHandler} className='w-full mb-[15px] p-[10px] border-[1px] border-[#c5c5c5] rounded-[4px] outline-orange-600' type='text' placeholder='Country'/>
            </div>
            <input required name='phone' onChange={onChangeHandler} className='w-full mb-[15px] p-[10px] border-[1px] border-[#c5c5c5] rounded-[4px] outline-orange-600' type='text' placeholder='Phone'/>
        </div>
        {/* Right view */}
        <div className='w-full sm:w-[40%]'>
            <div className='flex-1 flex flex-col gap-[20px]'>
                <h2 className='text-[30px] font-[600] mb-[30px]'>CART TOTAL</h2>
                <div>
                    <div className='flex justify-between text-[#555]'>
                        <p>Subtotal</p>
                        <p>${getTotalCartValue()}</p>
                    </div>
                    <hr className='h-[1px] border-[#e2e2e2] border my-[10px] mx-[0px]' />
                    <div className='flex justify-between text-[#555]'>
                        <p>Delivery Fee</p>
                        <p>${2}</p>
                    </div>
                    <hr className='h-[1px] border-[#e2e2e2] border my-[10px] mx-[0px]' />
                    <div className='flex justify-between text-[#555]'>
                        <b>Total</b>
                        <b>${getTotalCartValue() + 2}</b>
                    </div>
                </div>
                <button type='submit' disabled={getTotalCartValue() === 0} className='border border-gray-600 rounded-[4px] text-white bg-orange-600 hover:bg-orange-700 disabled:bg-gray-600 w-[200px] py-[12px] px-[0px] cursor-pointer'>Proceed To Payment</button>
            </div>
        </div>
    </form>
  )
}

export default PlaceOrder