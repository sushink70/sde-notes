class Solution {
public:

    void f(int ind,vector<int>& nums,vector<int>& v ,int &sum){
        if(ind==nums.size()) {
            int x=0;
            for(int i=0;i<v.size(); i++){
                x^=v[i];
            }
            sum+=x;
            return ;
        }    
        v.push_back(nums[ind]);
        f(ind+1,nums,v,sum);
        v.pop_back();
        f(ind+1,nums,v,sum);
        
    }
    int subsetXORSum(vector<int>& nums) {
        int sum=0;
        vector<int> v;
        f(0,nums,v,sum);
        return sum;
    }
};
