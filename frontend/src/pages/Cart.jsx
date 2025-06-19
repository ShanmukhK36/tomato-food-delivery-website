import { useContext } from 'react';
import { StoreContext } from '../context/StoreContext';
import { RxCross1 } from "react-icons/rx";
import { useNavigate } from 'react-router-dom';

const Cart = () => {
    const {cartItems, food_list, removeFromCart, getTotalCartValue, url} = useContext(StoreContext);
    const navigate = useNavigate(); 
    
  return (
    <div className='mt-[20px]'>
        <div>
            <div className='grid grid-cols-[1fr_1.1fr_1fr_1fr_0.8fr_0.5fr] md:grid-cols-[1fr_1.5fr_1fr_1fr_1fr_0.5fr] items-center text-gray-600 text-[15px]'>
                <p>Items</p>
                <p>Title</p>
                <p>Price</p>
                <p>Quantity</p>
                <p>Total</p>
                <p>Remove</p>
            </div>
            <br />
            <hr className='h-[1px] border border-[#e2e2e2]' />
            {food_list.map((item, index) => {
                if(cartItems[item._id] > 0) {
                    return (
                        <div>
                            <div className='grid grid-cols-[1fr_1.1fr_1fr_1fr_1fr_0.5fr] md:grid-cols-[1fr_1.5fr_1fr_1fr_1fr_0.5fr] items-center text-[15px] my-[10px] mx-[0px] text-black'>
                                <img className='w-[50px]' src={url+'/images/'+item.image}/>
                                <p>{item.name}</p>
                                <p>${item.price}</p>
                                <p>{cartItems[item._id]}</p>
                                <p>${item.price*cartItems[item._id]}</p>
                                <RxCross1 onClick={() => removeFromCart(item._id)} className='cursor-pointer' />
                            </div>
                            <hr className='h-[1px] border-[#e2e2e2] border' />
                        </div>
                    )
                }
            })}
        </div>
        <div className='mt-[80px] flex flex-col-reverse justify-between gap-[20px] sm:flex-row'>
            <div className='flex-1 flex flex-col gap-[20px]'>
                <h2>CART TOTAL</h2>
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
                <button disabled={getTotalCartValue() === 0} onClick={() => navigate('/order')} className='border border-gray-600 rounded-[4px] text-white bg-orange-600 hover:bg-orange-700 disabled:bg-gray-600 w-[200px] py-[12px] px-[0px] cursor-pointer'>Proceed To Checkout</button>
            </div>
            <div>
                <div className='flex-1 mb-5'>
                    <p className='text-[#555] text-[18px] font-[500]'>If you have a promo code, Enter it here</p>
                    <div className='mt-10 flex justify-between items-center bg-[#eaeaea] rounded-[4px]'>
                        <input className='border-none outline-none bg-transparent pl-[10px]' type='text' placeholder='Promo Code'/>
                        <button className='border rounded-[4px] w-[150px] py-[12px] px-[5px] bg-black text-white'>Submit</button>
                    </div>
                </div>
            </div>
        </div>
    </div>
  )
}

export default Cart