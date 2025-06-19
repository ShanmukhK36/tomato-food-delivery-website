import { useContext, useEffect, useState } from 'react';
import { StoreContext } from '../context/StoreContext';
import axios from 'axios';
import { assets } from '../assets/assets';

const MyOrders = () => {
    const {url, token} = useContext(StoreContext);
    const [data, setData] = useState([]);
    const fetchOrders = async () => {
        const response = await axios.post(url+'/api/order/userorders', {}, {headers: {token}});
        setData(response.data.data);
    }
    useEffect(() => {
        if(token) {
            fetchOrders();
        }
    }, [token])
  return (
    <div className='my-[50px] mx-[0px]'>
        <h2 className='font-medium text-[#454545] text-[30px]'>My Orders</h2>
        <div className='flex flex-col gap-[20px] mt-[30px]'>
            {data.map((order, index) => {
                return (
                    <div key={index} className='grid grid-cols-[0.5fr_2fr_1fr_1fr_2fr_1fr] items-center gap-[10px] md:gap-[30px] text-[14px] py-[10px] px-[10px] md:px-[20px] text-[#454545] border border-orange-600'>
                        <img className='w-[50px]' src={assets.parcel_icon}/>
                        <p>{order.items.map((item, index) => {
                            if(index === order.items.length - 1) {
                                return item.name + ' x ' + item.quantity;
                            } else {
                                return item.name + ' x ' + item.quantity + ', ';
                            }
                        })}</p>
                        <p>${order.amount}.00</p>
                        <p>Items: {order.items.length}</p>
                        <p className='flex items-center gap-2'><span className='text-orange-600'>{'\u25cf'}</span> <b className='font-[500] text-[#454545]'>{order.status}</b></p>
                        <button onClick={fetchOrders} className='border-none py-[12px] px-[0px] rounded-[4px] bg-[#ffe1e1] hover:bg-[#f5c0c0] cursor-pointer text-[#454545]'>Track Order</button>
                    </div>
                )
            })}
        </div>
    </div>
  )
}

export default MyOrders